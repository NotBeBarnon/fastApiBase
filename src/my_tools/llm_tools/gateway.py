# -*- coding: utf-8 -*-
# @Description : LLM 多模型网关核心实现
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ..observability.metrics import LLMMetrics
from ..observability.tracing import trace_llm_call
from .config import LLMConfig, LLMProvider, ProviderType

__all__ = ("LLMGateway", "LLMResponse", "LLMMessage")


# ---------------------------------------------------------------------------
#  数据结构
# ---------------------------------------------------------------------------
@dataclass
class LLMMessage:
    """对话消息"""

    role: str  # system / user / assistant / tool
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        return d


@dataclass
class LLMUsage:
    """Token 用量"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @property
    def cost_estimate(self) -> float:
        """粗略费用估算（基于 GPT-3.5 价格，仅作参考）"""
        # $0.0015 / 1K prompt, $0.002 / 1K completion
        return (self.prompt_tokens * 0.0015 + self.completion_tokens * 0.002) / 1000


@dataclass
class LLMResponse:
    """LLM 完整响应"""

    content: str
    model: str = ""
    provider: str = ""
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str = "stop"
    tool_calls: list[dict] = field(default_factory=list)
    latency_ms: float = 0.0
    raw: Any = None  # 原始响应对象


# ---------------------------------------------------------------------------
#  网关主体
# ---------------------------------------------------------------------------
class LLMGateway:
    """
    LLM 多模型网关。

    特性：
    - 统一接口：chat() / stream_chat() / embeddings()
    - 多厂商适配：OpenAI / Anthropic / Azure / 本地模型
    - 自动重试 + 降级：主 provider 失败自动切到 fallback
    - Token 统计 + 费用估算
    - 支持函数调用（Function Calling）
    - 支持 SSE 流式输出（与 SSEStream + LLMStreamer 对接）

    用法：
        config = LLMConfig(
            default_provider="deepseek",
            providers={
                "deepseek": LLMProvider(
                    name="deepseek",
                    type=ProviderType.OPENAI,
                    base_url="https://api.deepseek.com/v1",
                    api_key="sk-xxx",
                    default_model="deepseek-chat",
                )
            }
        )
        gateway = LLMGateway(config)

        resp = await gateway.chat([
            LLMMessage(role="system", content="你是一个助手"),
            LLMMessage(role="user", content="你好"),
        ])
        print(resp.content)
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._providers: dict[str, LLMProvider] = {}
        self._clients: dict[str, Any] = {}
        self._cost_total: float = 0.0
        self._call_count: int = 0
        self.metrics = LLMMetrics()

        # 初始化 provider
        for name, prov in config.providers.items():
            if prov.enabled:
                self._providers[name] = prov

        if not self._providers:
            logger.warning("LLMGateway: no enabled providers found")

    # ================================================================
    #  核心 API
    # ================================================================

    async def chat(
        self,
        messages: list[LLMMessage] | list[dict],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        非流式对话。

        Args:
            messages: 对话消息列表
            provider: 指定 provider，不填用默认
            model: 指定模型，不填用 provider 默认
            temperature: 温度
            max_tokens: 最大输出 token 数
            tools: Function Calling 工具列表（OpenAI 格式）
            **kwargs: 额外参数传给底层 SDK

        Returns:
            LLMResponse
        """
        return await self._try_with_fallback(
            "chat",
            messages=messages,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            **kwargs,
        )

    async def stream_chat(
        self,
        messages: list[LLMMessage] | list[dict],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """
        流式对话（产出纯文本增量）。

        可以直接接入 SSEStream：
            async def gen():
                async for chunk in gateway.stream_chat(messages):
                    yield chunk
            return SSEStream.from_generator(gen())
        """
        # 使用默认 provider（降级由内部实现处理）
        prov_name = provider or self.config.default_provider
        prov = self._providers.get(prov_name)
        if prov is None:
            raise ValueError(f"Provider not found: {prov_name}")

        model_name = model or prov.default_model
        start = time.time()
        async with trace_llm_call("stream_chat", provider=prov_name, model=model_name):
            try:
                async for chunk in self._stream_chat_provider(
                    prov,
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    **kwargs,
                ):
                    yield chunk
            except Exception:
                self.metrics.record(prov_name, success=False, latency_ms=(time.time() - start) * 1000)
                raise
            else:
                self.metrics.record(prov_name, success=True, latency_ms=(time.time() - start) * 1000)

    async def embeddings(
        self,
        texts: list[str],
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> list[list[float]]:
        """
        获取文本的 embedding 向量。
        """
        return await self._try_with_fallback(
            "embeddings",
            texts=texts,
            provider=provider,
            model=model,
        )

    # ================================================================
    #  降级重试机制
    # ================================================================

    async def _try_with_fallback(self, method: str, **kwargs) -> Any:
        """尝试主 provider，失败则按 fallback 顺序重试"""
        provider = kwargs.pop("provider", None) or self.config.default_provider
        fallback_chain = [provider] + [p for p in self.config.fallback_providers if p != provider]

        start = time.time()
        tried = 0
        last_exc: Exception | None = None
        async with trace_llm_call(method, provider=provider):
            for prov_name in fallback_chain:
                prov = self._providers.get(prov_name)
                if prov is None or not prov.enabled:
                    continue
                tried += 1
                if tried > 1:
                    self.metrics.record_fallback(prov_name)
                    logger.warning(f"LLM fallback -> '{prov_name}'")
                try:
                    result = await self._call_provider(prov, method, **kwargs)
                    if hasattr(result, "provider"):
                        result.provider = prov_name
                    self.metrics.record(
                        prov_name,
                        success=True,
                        latency_ms=(time.time() - start) * 1000,
                        prompt_tokens=getattr(getattr(result, "usage", None), "prompt_tokens", 0),
                        completion_tokens=getattr(getattr(result, "usage", None), "completion_tokens", 0),
                        total_tokens=getattr(getattr(result, "usage", None), "total_tokens", 0),
                        cost=getattr(getattr(result, "usage", None), "cost_estimate", 0.0),
                    )
                    return result
                except Exception as exc:
                    logger.warning(f"LLM provider '{prov_name}' failed: {exc}")
                    self.metrics.record(
                        prov_name,
                        success=False,
                        latency_ms=(time.time() - start) * 1000,
                    )
                    last_exc = exc
                    continue

        raise RuntimeError(f"All LLM providers failed. Last error: {last_exc}")

    async def _call_provider(self, prov: LLMProvider, method: str, **kwargs) -> Any:
        """调用单个 provider，带重试"""
        for attempt in range(prov.max_retries + 1):
            try:
                if method == "chat":
                    return await self._chat_provider(prov, **kwargs)
                elif method == "embeddings":
                    return await self._embeddings_provider(prov, **kwargs)
                else:
                    raise ValueError(f"Unknown method: {method}")
            except Exception as exc:
                if attempt < prov.max_retries:
                    wait = 0.5 * (2 ** attempt)
                    self.metrics.record_retry(prov.name)
                    logger.warning(f"LLM '{prov.name}' attempt {attempt+1} failed, retry in {wait}s: {exc}")
                    await asyncio.sleep(wait)
                else:
                    raise

    # ================================================================
    #  Provider 实现（基于 httpx 直接调用 HTTP API，零 SDK 依赖）
    # ================================================================

    async def _chat_provider(
        self,
        prov: LLMProvider,
        messages: list[LLMMessage] | list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> LLMResponse:
        """统一通过 httpx 调用 OpenAI 兼容接口"""
        import httpx

        start = time.time()
        url = f"{prov.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {prov.api_key}",
            "Content-Type": "application/json",
        }
        # Azure 兼容
        if prov.type == ProviderType.AZURE:
            headers["api-key"] = prov.api_key
            del headers["Authorization"]

        body: dict[str, Any] = {
            "model": model or prov.default_model,
            "messages": [m.to_dict() if isinstance(m, LLMMessage) else m for m in messages],
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        body.update(kwargs)

        timeout = prov.timeout or self.config.global_timeout

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        latency = (time.time() - start) * 1000
        choice = data["choices"][0]
        msg = choice.get("message", {})

        usage_data = data.get("usage", {})
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        result = LLMResponse(
            content=msg.get("content", "") or "",
            model=data.get("model", model or prov.default_model),
            provider=prov.name,
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop"),
            tool_calls=msg.get("tool_calls", []) or [],
            latency_ms=latency,
            raw=data,
        )

        return result

    async def _stream_chat_provider(
        self,
        prov: LLMProvider,
        messages: list[LLMMessage] | list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """流式对话（基于 httpx streaming）"""
        import httpx

        url = f"{prov.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {prov.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if prov.type == ProviderType.AZURE:
            headers["api-key"] = prov.api_key
            del headers["Authorization"]

        body: dict[str, Any] = {
            "model": model or prov.default_model,
            "messages": [m.to_dict() if isinstance(m, LLMMessage) else m for m in messages],
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        body.update(kwargs)

        timeout = prov.timeout or self.config.global_timeout

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def _embeddings_provider(
        self,
        prov: LLMProvider,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        """获取 embedding 向量"""
        import httpx

        url = f"{prov.base_url.rstrip('/')}/embeddings"
        headers = {
            "Authorization": f"Bearer {prov.api_key}",
            "Content-Type": "application/json",
        }
        if prov.type == ProviderType.AZURE:
            headers["api-key"] = prov.api_key
            del headers["Authorization"]

        body = {
            "model": model or prov.default_model.replace("chat", "embedding"),
            "input": texts,
        }

        timeout = prov.timeout or self.config.global_timeout

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        return [item["embedding"] for item in data["data"]]

    # ================================================================
    #  统计信息
    # ================================================================

    def _record_call(self, usage: LLMUsage) -> None:
        """兼容旧接口：统一由 metrics 采集，这里不再重复计数。"""

    @property
    def call_count(self) -> int:
        """累计调用次数（成功 + 失败）"""
        return self.metrics.calls

    @property
    def cost_total(self) -> float:
        """累计费用估算（美元）"""
        return round(self.metrics.cost_total, 6)

    def get_stats(self) -> dict:
        """获取网关统计信息（含延迟分位数、错误率、按 provider 拆分）"""
        return {
            "call_count": self.metrics.calls,
            "cost_total_usd": round(self.metrics.cost_total, 6),
            "providers": list(self._providers.keys()),
            "default_provider": self.config.default_provider,
            "enabled_providers": {
                name: {"type": p.type.value, "model": p.default_model, "priority": p.priority}
                for name, p in self._providers.items()
            },
            "metrics": self.metrics.snapshot(),
        }

    async def ping(self) -> dict[str, Any]:
        """
        深度健康检查：实际请求各 provider 的模型列表端点（GET /models）。

        Returns:
            {provider_name: {"ok": bool, "latency_ms": float, "error": str}}
        """
        import httpx

        async def _ping_one(name: str, prov: LLMProvider) -> tuple[str, dict[str, Any]]:
            start = time.time()
            url = f"{prov.base_url.rstrip('/')}/models"
            headers: dict[str, str] = {}
            if prov.type == ProviderType.AZURE:
                headers["api-key"] = prov.api_key
            else:
                headers["Authorization"] = f"Bearer {prov.api_key}"
            try:
                async with httpx.AsyncClient(timeout=prov.timeout or self.config.global_timeout) as client:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                return name, {"ok": True, "latency_ms": round((time.time() - start) * 1000, 2), "error": ""}
            except Exception as exc:
                return name, {
                    "ok": False,
                    "latency_ms": round((time.time() - start) * 1000, 2),
                    "error": f"{exc.__class__.__name__}: {exc}",
                }

        results = await asyncio.gather(
            *(_ping_one(name, prov) for name, prov in self._providers.items())
        )
        return {name: info for name, info in results}

    def health_check(self) -> dict:
        """健康检查：各 provider 连通性"""
        # 返回各 provider 的配置状态（不实际调用，避免产生费用）
        return {
            name: {
                "enabled": p.enabled,
                "type": p.type.value,
                "has_api_key": bool(p.api_key),
                "base_url": p.base_url,
                "default_model": p.default_model,
            }
            for name, p in self._providers.items()
        }
