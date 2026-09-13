# @Description : 健康检查探针（DB / Redis / Kafka / LLM）与状态聚合
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

__all__ = (
    "ComponentHealth",
    "check_database",
    "check_redis",
    "check_kafka",
    "check_llm",
    "build_app_health",
)

STATUS_UP = "up"
STATUS_DOWN = "down"
STATUS_DISABLED = "disabled"


@dataclass
class ComponentHealth:
    """单个依赖组件的健康状态。"""

    name: str
    status: str
    latency_ms: float = 0.0
    detail: str = ""
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 2),
            "detail": self.detail,
            "required": self.required,
        }


async def _timed(coro: Any) -> tuple[Any, float]:
    start = time.perf_counter()
    result = await coro
    return result, (time.perf_counter() - start) * 1000


async def check_database(timeout: float = 3.0) -> ComponentHealth:
    """Tortoise ORM 默认连接连通性（SELECT 1）。"""
    from tortoise import Tortoise
    from tortoise.exceptions import ConfigurationError

    start = time.perf_counter()
    try:
        async with asyncio.timeout(timeout):
            conn = Tortoise.get_connection("default")
            await conn.execute_query("SELECT 1")
        latency = (time.perf_counter() - start) * 1000
        return ComponentHealth("database", STATUS_UP, latency, "SELECT 1 ok")
    except ConfigurationError:
        return ComponentHealth("database", STATUS_DISABLED, 0.0, "Tortoise not initialized")
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        logger.warning(f"health: database down - {exc.__class__.__name__}: {exc}")
        return ComponentHealth("database", STATUS_DOWN, latency, f"{exc.__class__.__name__}: {exc}")


def _raw_redis(client: Any) -> Any:
    """从单连接 / 哨兵客户端中取出可执行命令的原生连接。"""
    master = getattr(client, "_master_redis", None)
    if master is not None:
        return master
    return getattr(client, "_pool_client", None)


async def check_redis(client: Any, timeout: float = 3.0) -> ComponentHealth:
    """Redis 连通性（PING），支持单连接与哨兵客户端。"""
    if client is None:
        return ComponentHealth("redis", STATUS_DISABLED, 0.0, "redis client not mounted")
    raw = _raw_redis(client)
    if raw is None:
        return ComponentHealth("redis", STATUS_DOWN, 0.0, "redis not connected (retrying)")
    start = time.perf_counter()
    try:
        async with asyncio.timeout(timeout):
            pong = await raw.ping()
        latency = (time.perf_counter() - start) * 1000
        if pong:
            return ComponentHealth("redis", STATUS_UP, latency, "PONG")
        return ComponentHealth("redis", STATUS_DOWN, latency, "ping returned falsy")
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        logger.warning(f"health: redis down - {exc.__class__.__name__}: {exc}")
        return ComponentHealth("redis", STATUS_DOWN, latency, f"{exc.__class__.__name__}: {exc}")


def _parse_server(server: str) -> tuple[str, int]:
    host, _, port = str(server).rpartition(":")
    return host or "localhost", int(port or "9092")


async def _tcp_reachable(server: str, timeout: float) -> bool:
    host, port = _parse_server(server)
    try:
        async with asyncio.timeout(timeout):
            _, writer = await asyncio.open_connection(host, port)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True
    except Exception:
        return False


async def check_kafka(bootstrap_servers: Any, timeout: float = 2.0) -> ComponentHealth:
    """Kafka broker 连通性（对 bootstrap 节点做 TCP 探测，任一可达即 up）。"""
    servers = [str(s) for s in bootstrap_servers]
    if not servers:
        return ComponentHealth("kafka", STATUS_DISABLED, 0.0, "no bootstrap servers", required=False)
    start = time.perf_counter()
    results = await asyncio.gather(*(_tcp_reachable(s, timeout) for s in servers))
    latency = (time.perf_counter() - start) * 1000
    reachable = [s for s, ok in zip(servers, results, strict=True) if ok]
    if reachable:
        return ComponentHealth(
            "kafka", STATUS_UP, latency, f"reachable: {','.join(reachable)}", required=False
        )
    return ComponentHealth(
        "kafka", STATUS_DOWN, latency, f"all {len(servers)} brokers unreachable", required=False
    )


async def check_llm(gateway: Any, *, deep: bool = False, timeout: float = 5.0) -> ComponentHealth:
    """
    LLM 网关健康状态。

    - 无 provider -> disabled
    - 浅检查（默认）：仅核对配置就绪，不产生费用
    - 深检查（deep=True）：实际 ping 各 provider 的 /models 端点
    """
    providers = getattr(gateway, "_providers", None) if gateway is not None else None
    if gateway is None or not providers:
        return ComponentHealth("llm", STATUS_DISABLED, 0.0, "no providers configured", required=False)
    if not deep:
        return ComponentHealth(
            "llm", STATUS_UP, 0.0, f"{len(providers)} providers configured", required=False
        )

    start = time.perf_counter()
    try:
        async with asyncio.timeout(timeout * max(1, len(providers))):
            report = await gateway.ping()
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return ComponentHealth("llm", STATUS_DOWN, latency, f"ping failed: {exc}", required=False)

    latency = (time.perf_counter() - start) * 1000
    down = {name: info for name, info in report.items() if not info.get("ok")}
    if down:
        return ComponentHealth(
            "llm",
            STATUS_DOWN,
            latency,
            f"{len(down)}/{len(report)} providers unreachable: {','.join(down)}",
            required=False,
        )
    return ComponentHealth("llm", STATUS_UP, latency, f"{len(report)} providers reachable", required=False)


def _aggregate(checks: list[ComponentHealth]) -> dict[str, Any]:
    """按 required 依赖聚合总体状态：up / degraded / down。"""
    any_required_down = any(c.status == STATUS_DOWN and c.required for c in checks)
    any_optional_down = any(c.status == STATUS_DOWN and not c.required for c in checks)
    if any_required_down:
        overall = STATUS_DOWN
    elif any_optional_down:
        overall = "degraded"
    else:
        overall = STATUS_UP
    return {
        "status": overall,
        "components": [c.to_dict() for c in checks],
    }


async def build_app_health(
    app: Any,
    *,
    deep_llm: bool = False,
    include_kafka: bool = True,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """从 FastAPI app.state 读取资源并并发执行全部探针。"""
    state = getattr(app, "state", app)
    checks: list[ComponentHealth] = [await check_database(timeout=timeout)]

    redis_client = getattr(state, "redis", None)
    checks.append(await check_redis(redis_client, timeout=timeout))

    kafka_client = getattr(state, "kafka", None)
    if include_kafka and kafka_client is not None:
        servers = getattr(kafka_client, "bootstrap_servers", [])
        checks.append(await check_kafka(servers, timeout=timeout))

    llm_gateway = getattr(state, "llm", None)
    checks.append(await check_llm(llm_gateway, deep=deep_llm))

    return _aggregate(checks)
