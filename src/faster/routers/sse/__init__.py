# -*- coding: utf-8 -*-
# @Description : SSE 流式响应示例路由
from __future__ import annotations

import asyncio
import random

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from src.my_tools.sse_tools import SSEStream, SSEEvent
from src.settings import HTTP_BASE_URL

sse_router = APIRouter(prefix="/sse", tags=["sse"])


@sse_router.get("/simple")
async def sse_simple(count: int = 10, interval: float = 0.5) -> StreamingResponse:
    """
    简单 SSE 流示例：按顺序发送数字。

    Args:
        count: 发送次数
        interval: 间隔秒数
    """
    async def gen():
        for i in range(count):
            yield SSEEvent(data={"index": i, "message": f"第 {i+1} 条消息"})
            await asyncio.sleep(interval)
        yield SSEEvent(data="流结束", event="done")

    return SSEStream.from_generator(gen())


@sse_router.get("/progress")
async def sse_progress(steps: int = 20, interval: float = 0.2) -> StreamingResponse:
    """
    进度条 SSE 示例：模拟长时间任务的进度反馈。
    """
    async def long_task(on_progress, steps: int, interval: float):
        for i in range(steps):
            await asyncio.sleep(interval)
            message = f"正在处理第 {i+1} 项..."
            if i == steps // 2:
                message = "已完成一半，继续加油！"
            on_progress(i + 1, steps, message)
        return {"status": "completed", "items": steps}

    return SSEStream.progress(long_task, steps=steps, interval=interval)


@sse_router.get("/random-numbers")
async def sse_random_numbers(count: int = 20, interval: float = 0.3) -> StreamingResponse:
    """
    随机数流式生成示例。
    """
    async def gen():
        for i in range(count):
            yield SSEEvent(
                data={"index": i, "value": random.randint(1, 100)},
                event="number",
                id=i,
            )
            await asyncio.sleep(interval)
        yield SSEEvent(data="[DONE]", event="done")

    return SSEStream.from_generator(gen())


@sse_router.get("/chat")
async def sse_chat_demo(message: str = "你好") -> StreamingResponse:
    """
    模拟 AI 对话流式输出示例（逐字输出）。
    """
    reply = f"收到你的消息：「{message}」。这是一个模拟的流式回复，每个字都会逐个输出，就像真实的 AI 对话一样。"

    async def gen():
        for char in reply:
            yield SSEEvent(data={"content": char, "role": "assistant"})
            await asyncio.sleep(0.05)
        yield SSEEvent(
            data={"finish_reason": "stop", "usage": {"prompt_tokens": len(message), "completion_tokens": len(reply)}},
            event="done",
        )

    return SSEStream.from_generator(gen())
