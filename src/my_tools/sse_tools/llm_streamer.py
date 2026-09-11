# @Description : LLM 流式输出封装（适配多模型厂商，统一 SSE 输出格式）
from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from .stream import SSEEvent

__all__ = ("LLMStreamer", "LLMChunk", "LLMStreamFormat")


@dataclass
class LLMChunk:
    """LLM 流式输出的统一 chunk 结构"""

    content: str = ""
    role: str | None = None  # "assistant" / "user" / "system"
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict | None = None  # token 统计
    finish_reason: str | None = None  # "stop" / "length" / "tool_calls"
    model: str | None = None
    extra: dict = field(default_factory=dict)


class LLMStreamFormat:
    """LLM 流式输出格式枚举（常用前端渲染格式）"""

    # OpenAI 兼容格式（data: {...} \\n\\n，用 [DONE] 标记结束）
    OPENAI = "openai"
    # 简单文本格式（纯文本增量，data: chunk\\n\\n）
    PLAIN = "plain"
    # 事件类型格式（event: message / delta / done）
    EVENT = "event"


class LLMStreamer:
    """
    LLM 流式输出统一封装。

    负责：
    1. 将不同厂商的流式响应（OpenAI / Anthropic / DeepSeek / 本地模型）
       统一转换为 LLMChunk
    2. 将 LLMChunk 格式化为前端需要的 SSE 事件流
    3. 统计 token 使用量、记录日志、错误处理

    用法 — 配合 SSEStream.from_generator 使用：
        async def my_llm_stream(prompt):
            streamer = LLMStreamer(fmt=LLMStreamFormat.OPENAI)
            async for chunk in streamer.from_openai(openai_stream):
                yield chunk

        @app.get("/chat")
        async def chat(prompt: str):
            openai_stream = await client.chat.completions.create(
                model="gpt-4", messages=[{"role":"user","content":prompt}],
                stream=True
            )
            streamer = LLMStreamer(fmt=LLMStreamFormat.OPENAI)
            return SSEStream.from_generator(
                streamer.generate(openai_stream, source="openai"),
                event_name=None,
            )
    """

    def __init__(
        self,
        fmt: str = LLMStreamFormat.OPENAI,
        *,
        event_prefix: str = "",
        include_usage: bool = True,
        on_complete: Callable[[LLMChunk], None] | None = None,
    ) -> None:
        """
        Args:
            fmt: 输出格式（OPENAI / PLAIN / EVENT）
            event_prefix: 事件名前缀（用于多模型区分，如 "gpt-"）
            include_usage: 是否在流末尾包含 token 统计
            on_complete: 流结束时的回调（用于记录日志、统计等）
        """
        self.fmt = fmt
        self.event_prefix = event_prefix
        self.include_usage = include_usage
        self.on_complete = on_complete

        self._full_content = ""
        self._usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._tool_calls: list[dict] = []
        self._finish_reason: str | None = None
        self._model: str | None = None

    # ================================================================
    #  输入适配器：各种来源 → LLMChunk
    # ================================================================

    async def from_openai(self, stream: Any) -> AsyncGenerator[LLMChunk, None]:
        """
        适配 OpenAI 兼容的流式响应（也适用于 DeepSeek / 通义 / 豆包 等兼容接口）。

        stream 是 openai.AsyncStream[ChatCompletionChunk] 或类似对象。
        """
        try:
            async for chunk in stream:
                if hasattr(chunk, "choices") and chunk.choices:
                    choice = chunk.choices[0]
                    delta = choice.delta if hasattr(choice, "delta") else None

                    content = ""
                    tool_calls = []

                    if delta is not None:
                        if hasattr(delta, "content") and delta.content:
                            content = delta.content
                            self._full_content += content
                        if hasattr(delta, "tool_calls") and delta.tool_calls:
                            for tc in delta.tool_calls:
                                tool_calls.append({
                                    "index": tc.index if hasattr(tc, "index") else 0,
                                    "id": tc.id if hasattr(tc, "id") else None,
                                    "type": tc.type if hasattr(tc, "type") else "function",
                                    "function": {
                                        "name": tc.function.name if hasattr(tc.function, "name") else None,
                                        "arguments": tc.function.arguments if hasattr(tc.function, "arguments") else "",
                                    },
                                })
                            self._tool_calls.extend(tool_calls)

                    finish_reason = choice.finish_reason if hasattr(choice, "finish_reason") else None
                    if finish_reason:
                        self._finish_reason = finish_reason

                    if hasattr(chunk, "model") and chunk.model:
                        self._model = chunk.model

                    yield LLMChunk(
                        content=content,
                        role=delta.role if delta and hasattr(delta, "role") and delta.role else None,
                        tool_calls=tool_calls,
                        finish_reason=finish_reason,
                        model=chunk.model if hasattr(chunk, "model") else None,
                    )

                # usage（最后一条消息可能带 usage）
                if hasattr(chunk, "usage") and chunk.usage:
                    u = chunk.usage
                    self._usage = {
                        "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                        "total_tokens": getattr(u, "total_tokens", 0) or 0,
                    }
        except Exception as exc:
            logger.exception(f"LLM stream error: {exc}")
            yield LLMChunk(content=f"\n\n[Error] {exc}", finish_reason="error")

    async def from_text_generator(self, gen: AsyncGenerator[str, None]) -> AsyncGenerator[LLMChunk, None]:
        """
        适配纯文本异步生成器（本地模型、自定义逻辑等）。
        """
        async for text in gen:
            self._full_content += text
            yield LLMChunk(content=text)
        self._finish_reason = "stop"

    async def from_anthropic(self, stream: Any) -> AsyncGenerator[LLMChunk, None]:
        """
        适配 Anthropic Claude 流式响应。
        """
        try:
            async for event in stream:
                etype = getattr(event, "type", "")
                if etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta and hasattr(delta, "text"):
                        text = delta.text
                        self._full_content += text
                        yield LLMChunk(content=text)
                elif etype == "message_stop":
                    self._finish_reason = "stop"
                    yield LLMChunk(content="", finish_reason="stop")
                elif etype == "message_start":
                    msg = getattr(event, "message", None)
                    if msg and hasattr(msg, "model"):
                        self._model = msg.model
                        yield LLMChunk(content="", role="assistant", model=msg.model)
        except Exception as exc:
            logger.exception(f"Anthropic stream error: {exc}")
            yield LLMChunk(content=f"\n\n[Error] {exc}", finish_reason="error")

    # ================================================================
    #  输出格式化：LLMChunk → SSEEvent
    # ================================================================

    async def generate(
        self,
        source_stream: Any,
        *,
        source: str = "openai",
    ) -> AsyncGenerator[SSEEvent, None]:
        """
        完整管道：从来源流读取 → 统一为 LLMChunk → 格式化为 SSEEvent。

        Args:
            source_stream: 原始流式响应对象
            source: 来源类型（"openai" / "anthropic" / "text"）
        """
        # 选择适配器
        if source == "openai":
            chunk_iter = self.from_openai(source_stream)
        elif source == "anthropic":
            chunk_iter = self.from_anthropic(source_stream)
        elif source == "text":
            chunk_iter = self.from_text_generator(source_stream)
        else:
            raise ValueError(f"Unknown source: {source}")

        # 格式化输出
        async for chunk in chunk_iter:
            for event in self._format_chunk(chunk):
                yield event

        # 结束事件
        if self.include_usage and self._usage.get("total_tokens", 0) > 0:
            yield self._make_event(
                data={"usage": self._usage, "model": self._model},
                event="usage",
            )

        yield self._make_event(data="[DONE]", event="done")

        # 完成回调
        if self.on_complete:
            final = LLMChunk(
                content=self._full_content,
                tool_calls=self._tool_calls,
                usage=self._usage,
                finish_reason=self._finish_reason,
                model=self._model,
            )
            try:
                if hasattr(self.on_complete, "__await__"):
                    await self.on_complete(final)
                else:
                    self.on_complete(final)
            except Exception:
                logger.exception("LLMStreamer on_complete callback error")

    def _format_chunk(self, chunk: LLMChunk) -> list[SSEEvent]:
        """将一个 LLMChunk 格式化为 SSEEvent 列表"""
        events: list[SSEEvent] = []

        if self.fmt == LLMStreamFormat.OPENAI:
            # OpenAI 兼容格式：{"choices":[{"delta":{"content":"..."}}]}
            payload = {
                "id": f"chatcmpl-{id(chunk)}",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": chunk.model or "",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": chunk.finish_reason,
                    }
                ],
            }
            if chunk.role:
                payload["choices"][0]["delta"]["role"] = chunk.role
            if chunk.content:
                payload["choices"][0]["delta"]["content"] = chunk.content
            if chunk.tool_calls:
                payload["choices"][0]["delta"]["tool_calls"] = chunk.tool_calls
            events.append(self._make_event(data=payload))

        elif self.fmt == LLMStreamFormat.PLAIN:
            # 纯文本格式
            if chunk.content:
                events.append(self._make_event(data=chunk.content))
            if chunk.finish_reason:
                events.append(self._make_event(data=chunk.finish_reason, event="finish"))

        elif self.fmt == LLMStreamFormat.EVENT:
            # 事件格式
            if chunk.content:
                events.append(self._make_event(data={"content": chunk.content}, event="delta"))
            if chunk.role:
                events.append(self._make_event(data={"role": chunk.role}, event="role"))
            if chunk.tool_calls:
                events.append(self._make_event(data={"tool_calls": chunk.tool_calls}, event="tool_calls"))
            if chunk.finish_reason:
                events.append(self._make_event(data={"reason": chunk.finish_reason}, event="finish"))

        return events

    def _make_event(self, data: Any, event: str | None = None) -> SSEEvent:
        event_name = f"{self.event_prefix}{event}" if event and self.event_prefix else event
        return SSEEvent(data=data, event=event_name)

    # ================================================================
    #  便捷属性
    # ================================================================

    @property
    def full_content(self) -> str:
        """已收到的完整文本"""
        return self._full_content

    @property
    def usage(self) -> dict:
        """token 用量统计"""
        return self._usage

    @property
    def finish_reason(self) -> str | None:
        """结束原因"""
        return self._finish_reason
