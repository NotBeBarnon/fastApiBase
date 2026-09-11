# @Description : 轻量指标采集（计数器 / 费用 / Token / 延迟直方图 + Prometheus 渲染）
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

__all__ = ("Histogram", "LLMMetrics")

_DEFAULT_BUCKETS_MS: tuple[float, ...] = (
    100,
    300,
    500,
    1000,
    2000,
    3000,
    5000,
    10000,
)


class Histogram:
    """固定容量滑动窗口直方图，支持平均值与 P50/P90/P95/P99 分位数。"""

    def __init__(self, max_samples: int = 1024, buckets: tuple[float, ...] = _DEFAULT_BUCKETS_MS) -> None:
        self._samples: deque[float] = deque(maxlen=max_samples)
        self.buckets = tuple(buckets)

    def observe(self, value: float) -> None:
        self._samples.append(float(value))

    def percentile(self, p: float) -> float:
        if not self._samples:
            return 0.0
        ordered = sorted(self._samples)
        rank = (len(ordered) - 1) * p
        low = int(rank)
        high = min(low + 1, len(ordered) - 1)
        if low == high:
            return round(ordered[low], 3)
        weight = rank - low
        return round(ordered[low] * (1 - weight) + ordered[high] * weight, 3)

    def snapshot(self) -> dict[str, Any]:
        if not self._samples:
            return {"count": 0, "avg_ms": 0.0, "p50_ms": 0.0, "p90_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0}
        values = list(self._samples)
        return {
            "count": len(values),
            "avg_ms": round(sum(values) / len(values), 3),
            "p50_ms": self.percentile(0.50),
            "p90_ms": self.percentile(0.90),
            "p95_ms": self.percentile(0.95),
            "p99_ms": self.percentile(0.99),
        }


@dataclass
class _ProviderCounters:
    """单个 provider 的累计计数。"""

    calls: int = 0
    successes: int = 0
    failures: int = 0
    retries: int = 0
    fallbacks: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    latency: Histogram = field(default_factory=Histogram)


class LLMMetrics:
    """
    LLM 调用指标采集器。

    - 全局计数器：调用 / 成功 / 失败 / 重试 / 降级 / Token / 费用
    - 按 provider 拆分标签
    - 延迟直方图（滑动窗口，P50/P90/P95/P99）
    - render_prometheus() 输出 Prometheus 文本格式
    """

    def __init__(self, max_samples: int = 1024) -> None:
        self.calls = 0
        self.successes = 0
        self.failures = 0
        self.retries = 0
        self.fallbacks = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cost_total = 0.0
        self.started_at = time.time()
        self.latency = Histogram(max_samples=max_samples)
        self._providers: dict[str, _ProviderCounters] = {}

    def _p(self, provider: str) -> _ProviderCounters:
        counters = self._providers.get(provider)
        if counters is None:
            counters = _ProviderCounters()
            self._providers[provider] = counters
        return counters

    def record_retry(self, provider: str) -> None:
        self.retries += 1
        self._p(provider).retries += 1

    def record_fallback(self, provider: str) -> None:
        self.fallbacks += 1
        self._p(provider).fallbacks += 1

    def record(
        self,
        provider: str,
        *,
        success: bool,
        latency_ms: float = 0.0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cost: float = 0.0,
    ) -> None:
        self.calls += 1
        self.latency.observe(latency_ms)
        counters = self._p(provider)
        counters.calls += 1
        counters.latency.observe(latency_ms)

        if success:
            self.successes += 1
            counters.successes += 1
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens
            self.total_tokens += total_tokens or (prompt_tokens + completion_tokens)
            self.cost_total += cost
            counters.prompt_tokens += prompt_tokens
            counters.completion_tokens += completion_tokens
            counters.total_tokens += total_tokens or (prompt_tokens + completion_tokens)
            counters.cost += cost
        else:
            self.failures += 1
            counters.failures += 1

    @property
    def error_rate(self) -> float:
        if self.calls == 0:
            return 0.0
        return round(self.failures / self.calls, 4)

    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self.started_at)

    def snapshot(self) -> dict[str, Any]:
        """导出全部指标（JSON 结构）。"""
        return {
            "uptime_seconds": self.uptime_seconds,
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "error_rate": self.error_rate,
            "retries": self.retries,
            "fallbacks": self.fallbacks,
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": self.total_tokens,
            },
            "cost_total_usd": round(self.cost_total, 6),
            "latency_ms": self.latency.snapshot(),
            "providers": {
                name: {
                    "calls": c.calls,
                    "successes": c.successes,
                    "failures": c.failures,
                    "retries": c.retries,
                    "fallbacks": c.fallbacks,
                    "tokens": {
                        "prompt": c.prompt_tokens,
                        "completion": c.completion_tokens,
                        "total": c.total_tokens,
                    },
                    "cost_usd": round(c.cost, 6),
                    "latency_ms": c.latency.snapshot(),
                }
                for name, c in sorted(self._providers.items())
            },
        }

    def render_prometheus(self) -> str:
        """渲染为 Prometheus exposition format（文本）。"""
        lines: list[str] = [
            "# HELP llm_calls_total Total LLM calls by provider and status.",
            "# TYPE llm_calls_total counter",
        ]
        for name, c in sorted(self._providers.items()):
            lines.append(f'llm_calls_total{{provider="{name}",status="success"}} {c.successes}')
            lines.append(f'llm_calls_total{{provider="{name}",status="failure"}} {c.failures}')
        lines.append(f'llm_calls_total{{provider="_all",status="success"}} {self.successes}')
        lines.append(f'llm_calls_total{{provider="_all",status="failure"}} {self.failures}')

        lines += [
            "# HELP llm_retries_total Total provider-level retries.",
            "# TYPE llm_retries_total counter",
            f"llm_retries_total {self.retries}",
            "# HELP llm_fallbacks_total Total fallback switches between providers.",
            "# TYPE llm_fallbacks_total counter",
            f"llm_fallbacks_total {self.fallbacks}",
            "# HELP llm_tokens_total Token usage by kind.",
            "# TYPE llm_tokens_total counter",
            f'llm_tokens_total{{kind="prompt"}} {self.prompt_tokens}',
            f'llm_tokens_total{{kind="completion"}} {self.completion_tokens}',
            f'llm_tokens_total{{kind="total"}} {self.total_tokens}',
            "# HELP llm_cost_total_usd Estimated cost in USD.",
            "# TYPE llm_cost_total_usd counter",
            f"llm_cost_total_usd {round(self.cost_total, 6)}",
            "# HELP llm_request_latency_ms LLM request latency by quantile.",
            "# TYPE llm_request_latency_ms gauge",
        ]
        snap = self.latency.snapshot()
        if snap["count"]:
            lines += [
                f'llm_request_latency_ms{{quantile="0.5"}} {snap["p50_ms"]}',
                f'llm_request_latency_ms{{quantile="0.9"}} {snap["p90_ms"]}',
                f'llm_request_latency_ms{{quantile="0.95"}} {snap["p95_ms"]}',
                f'llm_request_latency_ms{{quantile="0.99"}} {snap["p99_ms"]}',
                f'llm_request_latency_ms{{quantile="avg"}} {snap["avg_ms"]}',
            ]
        return "\n".join(lines) + "\n"
