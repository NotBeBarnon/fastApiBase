# -*- coding: utf-8 -*-
# @Description : 可观测性模块测试（指标 / 追踪 / 健康探针 / 网关埋点）
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.my_tools.llm_tools import LLMGateway, LLMConfig, LLMMessage, LLMResponse, LLMUsage, LLMProvider, ProviderType
from src.my_tools.observability.health import (
    ComponentHealth,
    STATUS_DOWN,
    STATUS_UP,
    build_app_health,
    check_kafka,
    check_llm,
    check_redis,
)
from src.my_tools.observability.metrics import Histogram, LLMMetrics
from src.my_tools.observability.tracing import get_trace_id, trace_llm_call


def make_config(*, retries: int = 0) -> LLMConfig:
    return LLMConfig(
        default_provider="primary",
        fallback_providers=["backup"],
        providers={
            "primary": LLMProvider(
                name="primary",
                type=ProviderType.OPENAI,
                base_url="https://primary.api/v1",
                api_key="key-primary",
                default_model="primary-model",
                timeout=5,
                max_retries=retries,
                enabled=True,
            ),
            "backup": LLMProvider(
                name="backup",
                type=ProviderType.OPENAI,
                base_url="https://backup.api/v1",
                api_key="key-backup",
                default_model="backup-model",
                timeout=5,
                max_retries=0,
                enabled=True,
            ),
        },
    )


# ---------------------------------------------------------------------------
#  Histogram
# ---------------------------------------------------------------------------
def test_histogram_empty():
    print("\nTest 1: 空直方图")
    h = Histogram()
    snap = h.snapshot()
    assert snap["count"] == 0
    assert snap["p95_ms"] == 0.0
    assert h.percentile(0.99) == 0.0
    print("  ✅ 通过")


def test_histogram_percentiles():
    print("\nTest 2: 直方图分位数")
    h = Histogram()
    for i in range(1, 101):
        h.observe(float(i))
    snap = h.snapshot()
    assert snap["count"] == 100
    assert snap["avg_ms"] == 50.5
    assert snap["p50_ms"] >= 49
    assert snap["p95_ms"] >= 94
    assert snap["p99_ms"] >= 99
    print(f"  avg={snap['avg_ms']} p50={snap['p50_ms']} p95={snap['p95_ms']} p99={snap['p99_ms']}")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  LLMMetrics
# ---------------------------------------------------------------------------
def test_metrics_record():
    print("\nTest 3: 指标记录（成功/失败/Token/费用）")
    m = LLMMetrics()
    m.record("primary", success=True, latency_ms=120, prompt_tokens=100, completion_tokens=50, total_tokens=150, cost=0.001)
    m.record("primary", success=False, latency_ms=800)
    m.record("backup", success=True, latency_ms=300, prompt_tokens=10, completion_tokens=5, total_tokens=15, cost=0.0001)

    assert m.calls == 3
    assert m.successes == 2
    assert m.failures == 1
    assert m.total_tokens == 165
    assert 0 < m.error_rate < 1
    snap = m.snapshot()
    assert snap["providers"]["primary"]["failures"] == 1
    assert snap["latency_ms"]["count"] == 3
    print(f"  calls={m.calls} error_rate={m.error_rate} tokens={m.total_tokens}")
    print("  ✅ 通过")


def test_metrics_retry_fallback():
    print("\nTest 4: 重试 / 降级计数")
    m = LLMMetrics()
    m.record_retry("primary")
    m.record_retry("primary")
    m.record_fallback("backup")
    assert m.retries == 2
    assert m.fallbacks == 1
    assert m.snapshot()["providers"]["primary"]["retries"] == 2
    print("  ✅ 通过")


def test_render_prometheus():
    print("\nTest 5: Prometheus 文本渲染")
    m = LLMMetrics()
    m.record("primary", success=True, latency_ms=100, total_tokens=10)
    text = m.render_prometheus()
    assert "# TYPE llm_calls_total counter" in text
    assert 'llm_calls_total{provider="primary",status="success"} 1' in text
    assert "llm_tokens_total" in text
    assert 'quantile="0.95"' in text
    print("  含 counters / tokens / latency quantiles")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  Tracing
# ---------------------------------------------------------------------------
async def test_tracing_success():
    print("\nTest 6: 追踪上下文（成功）")
    assert get_trace_id() is None
    async with trace_llm_call("chat", provider="primary") as ctx:
        tid = get_trace_id()
        assert tid is not None and len(tid) == 32
        ctx["tokens"] = 42
    assert get_trace_id() is None
    assert ctx["latency_ms"] >= 0
    print(f"  trace_id={tid[:8]}... latency_ms={ctx['latency_ms']}")
    print("  ✅ 通过")


async def test_tracing_failure():
    print("\nTest 7: 追踪上下文（异常透传）")
    try:
        async with trace_llm_call("chat"):
            raise ValueError("boom")
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError 未透传")
    assert get_trace_id() is None
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  健康探针
# ---------------------------------------------------------------------------
class _FakeRedis:
    def __init__(self, raw):
        self._pool_client = raw


class _RawRedis:
    def __init__(self, fail: bool = False):
        self.fail = fail

    async def ping(self):
        if self.fail:
            raise ConnectionError("redis down")
        return True


async def test_check_redis():
    print("\nTest 8: Redis 探针")
    disabled = await check_redis(None)
    assert disabled.status == "disabled"

    up = await check_redis(_FakeRedis(_RawRedis()))
    assert up.status == STATUS_UP and "PONG" in up.detail

    not_connected = await check_redis(_FakeRedis(None))
    assert not_connected.status == STATUS_DOWN

    down = await check_redis(_FakeRedis(_RawRedis(fail=True)))
    assert down.status == STATUS_DOWN
    print("  disabled / up / not-connected / down 四种状态正确")
    print("  ✅ 通过")


async def test_check_kafka():
    print("\nTest 9: Kafka TCP 探针")
    disabled = await check_kafka([])
    assert disabled.status == "disabled"

    down = await check_kafka(["127.0.0.1:1"], timeout=1)
    assert down.status == STATUS_DOWN
    assert "unreachable" in down.detail
    print("  空配置=disabled，不可达端口=down")
    print("  ✅ 通过")


async def test_check_llm():
    print("\nTest 10: LLM 探针（浅 / 深）")
    disabled = await check_llm(None)
    assert disabled.status == "disabled"

    gateway = LLMGateway(make_config())
    shallow = await check_llm(gateway)
    assert shallow.status == STATUS_UP

    async def ping_all_ok():
        return {"primary": {"ok": True, "latency_ms": 5, "error": ""}}

    async def ping_with_down():
        return {"primary": {"ok": False, "latency_ms": 5, "error": "ConnectError"}}

    with patch.object(gateway, "ping", side_effect=ping_all_ok):
        assert (await check_llm(gateway, deep=True)).status == STATUS_UP
    with patch.object(gateway, "ping", side_effect=ping_with_down):
        assert (await check_llm(gateway, deep=True)).status == STATUS_DOWN
    print("  disabled / 浅检查 up / 深检查 up / 深检查 down")
    print("  ✅ 通过")


async def test_build_app_health():
    print("\nTest 11: 聚合健康状态")
    gateway = LLMGateway(LLMConfig(default_provider="", providers={}))
    state = SimpleNamespace(redis=None, llm=gateway, kafka=None)

    async def db_up(timeout=3.0):
        return ComponentHealth("database", STATUS_UP)

    async def db_down(timeout=3.0):
        return ComponentHealth("database", STATUS_DOWN, detail="conn refused")

    with patch("src.my_tools.observability.health.check_database", side_effect=db_up):
        healthy = await build_app_health(state, include_kafka=False)
    assert healthy["status"] == STATUS_UP
    names = {c["name"] for c in healthy["components"]}
    assert names == {"database", "redis", "llm"}

    with patch("src.my_tools.observability.health.check_database", side_effect=db_down):
        broken = await build_app_health(state, include_kafka=False)
    assert broken["status"] == STATUS_DOWN
    print(f"  正常={healthy['status']}，DB 故障={broken['status']}")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  网关埋点集成
# ---------------------------------------------------------------------------
async def test_gateway_metrics_success():
    print("\nTest 12: 网关成功调用 -> 指标")
    gateway = LLMGateway(make_config())
    expected = LLMResponse(
        content="ok",
        model="primary-model",
        usage=LLMUsage(prompt_tokens=10, completion_tokens=6, total_tokens=16),
    )

    async def mock_chat(prov, **kw):
        return expected

    with patch.object(gateway, "_chat_provider", side_effect=mock_chat):
        resp = await gateway.chat([LLMMessage(role="user", content="hi")])

    assert resp.content == "ok"
    assert gateway.metrics.calls == 1
    assert gateway.metrics.successes == 1
    assert gateway.metrics.failures == 0
    assert gateway.metrics.total_tokens == 16
    assert gateway.metrics.cost_total > 0
    assert gateway.call_count == 1
    assert gateway.metrics.latency.snapshot()["count"] == 1
    print(f"  calls=1 tokens=16 cost={gateway.metrics.cost_total:.6f}")
    print("  ✅ 通过")


async def test_gateway_metrics_fallback():
    print("\nTest 13: 主 provider 失败 -> 降级 + 失败/成功各计一次")
    gateway = LLMGateway(make_config())

    async def mock_chat(prov, **kw):
        if prov.name == "primary":
            raise ConnectionError("primary down")
        return LLMResponse(content="from-backup", model="backup-model")

    async def noop(_):
        return None

    with patch.object(gateway, "_chat_provider", side_effect=mock_chat), patch(
        "src.my_tools.llm_tools.gateway.asyncio.sleep", side_effect=noop
    ):
        resp = await gateway.chat([LLMMessage(role="user", content="hi")])

    assert resp.provider == "backup"
    assert gateway.metrics.successes == 1
    assert gateway.metrics.failures == 1
    assert gateway.metrics.fallbacks == 1
    print(f"  fallback -> {resp.provider}, fallbacks={gateway.metrics.fallbacks}")
    print("  ✅ 通过")


async def test_gateway_metrics_all_fail():
    print("\nTest 14: 全部 provider 失败 -> 抛错 + 失败计数")
    gateway = LLMGateway(make_config())

    async def mock_chat(prov, **kw):
        raise RuntimeError("all down")

    raised = False
    with patch.object(gateway, "_chat_provider", side_effect=mock_chat):
        try:
            await gateway.chat([LLMMessage(role="user", content="hi")])
        except RuntimeError:
            raised = True
    assert raised
    assert gateway.metrics.failures == 2
    assert gateway.metrics.successes == 0
    assert gateway.metrics.calls == 2
    print(f"  failures={gateway.metrics.failures}")
    print("  ✅ 通过")


async def test_gateway_metrics_retry():
    print("\nTest 15: provider 内部重试 -> retries 计数")
    cfg = LLMConfig(
        default_provider="only",
        providers={
            "only": LLMProvider(
                name="only",
                type=ProviderType.OPENAI,
                base_url="https://only.api/v1",
                api_key="k",
                default_model="m",
                max_retries=1,
            )
        },
    )
    gateway = LLMGateway(cfg)
    attempts = {"n": 0}

    async def flaky(prov, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("transient")
        return LLMResponse(content="recovered")

    async def noop(_):
        return None

    with patch.object(gateway, "_chat_provider", side_effect=flaky), patch(
        "src.my_tools.llm_tools.gateway.asyncio.sleep", side_effect=noop
    ):
        resp = await gateway.chat([{"role": "user", "content": "hi"}])

    assert resp.content == "recovered"
    assert attempts["n"] == 2
    assert gateway.metrics.retries == 1
    assert gateway.metrics.successes == 1
    print(f"  attempts=2 retries={gateway.metrics.retries}")
    print("  ✅ 通过")


async def test_gateway_stream_metrics():
    print("\nTest 16: 流式调用 -> 指标")
    gateway = LLMGateway(make_config())

    async def mock_stream(prov, messages, **kw):
        for c in ["a", "b", "c"]:
            yield c

    with patch.object(gateway, "_stream_chat_provider", side_effect=mock_stream):
        chunks = [c async for c in gateway.stream_chat([LLMMessage(role="user", content="hi")])]

    assert "".join(chunks) == "abc"
    assert gateway.metrics.calls == 1
    assert gateway.metrics.successes == 1
    print("  stream success recorded")
    print("  ✅ 通过")


async def main():
    print("\n🚀 可观测性模块测试开始\n")
    try:
        test_histogram_empty()
        test_histogram_percentiles()
        test_metrics_record()
        test_metrics_retry_fallback()
        test_render_prometheus()
        await test_tracing_success()
        await test_tracing_failure()
        await test_check_redis()
        await test_check_kafka()
        await test_check_llm()
        await test_build_app_health()
        await test_gateway_metrics_success()
        await test_gateway_metrics_fallback()
        await test_gateway_metrics_all_fail()
        await test_gateway_metrics_retry()
        await test_gateway_stream_metrics()
        print("\n" + "=" * 60)
        print("🎉 所有可观测性模块测试通过！（16 项）")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
