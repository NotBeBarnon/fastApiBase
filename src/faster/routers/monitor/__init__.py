# @Description : 监控端点（liveness / readiness 探针 + Prometheus 指标）
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from src.my_tools.observability.health import STATUS_DOWN, build_app_health

monitor_router = APIRouter(prefix="/monitor", tags=["monitor"])


@monitor_router.get("/healthz", summary="存活探针（进程是否存活）")
async def healthz() -> dict:
    """liveness：只要进程能响应即正常，不检查下游依赖。"""
    return {"status": "up"}


@monitor_router.get("/readyz", summary="就绪探针（下游依赖是否可用）")
async def readyz(
    request: Request,
    deep_llm: bool = Query(default=False, description="是否实际 ping LLM provider（产生请求）"),
    include_kafka: bool = Query(default=True, description="是否探测 Kafka broker"),
):
    """
    readiness：并发检查 DB / Redis / Kafka / LLM。

    - 必需依赖（DB）不可用 -> 503
    - 仅可选依赖（Redis 视为核心、Kafka/LLM 可选）异常 -> 200 + degraded
    """
    health = await build_app_health(
        request.app,
        deep_llm=deep_llm,
        include_kafka=include_kafka,
    )
    status_code = 503 if health["status"] == STATUS_DOWN else 200
    return JSONResponse(content=health, status_code=status_code)


@monitor_router.get("/metrics", summary="Prometheus 指标")
async def prometheus_metrics(request: Request, format: str = Query(default="prometheus")):
    """
    LLM 调用指标。

    - format=prometheus（默认）：Prometheus exposition text
    - format=json：完整 JSON 快照（含 P50/P90/P95/P99）
    """
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        return PlainTextResponse("", status_code=200)
    if format == "json":
        return llm.metrics.snapshot()
    return PlainTextResponse(
        llm.metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
