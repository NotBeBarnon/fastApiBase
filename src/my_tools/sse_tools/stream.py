# @Description : SSE 流式响应基础组件
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Any

from fastapi.responses import StreamingResponse
from loguru import logger

__all__ = ("SSEEvent", "SSEStream")


@dataclass
class SSEEvent:
    """
    SSE 事件数据结构。

    字段说明（参考 Server-Sent Events 规范）：
    - data: 事件数据（必填），可以是字符串、dict 或 list
    - event: 事件类型（可选），不填则默认 message
    - id: 事件 ID（可选），用于断线重连
    - retry: 重连间隔毫秒数（可选）
    """

    data: Any
    event: str | None = None
    id: str | int | None = None
    retry: int | None = None  # milliseconds

    def format(self) -> str:
        """格式化为 SSE 协议文本行"""
        lines: list[str] = []

        if self.event is not None:
            lines.append(f"event: {self.event}")
        if self.id is not None:
            lines.append(f"id: {self.id}")
        if self.retry is not None:
            lines.append(f"retry: {self.retry}")

        # data 字段：dict/list 序列化为 JSON，其他转字符串
        if isinstance(self.data, (dict, list)):
            data_str = json.dumps(self.data, ensure_ascii=False, default=str)
        else:
            data_str = str(self.data)

        # 处理多行 data
        for line in data_str.split("\n"):
            lines.append(f"data: {line}")

        return "\n".join(lines) + "\n\n"


class SSEStream:
    """
    SSE 流管理器。

    用法 1 — 生成器模式（最简单）：
        @app.get("/stream")
        async def stream():
            return SSEStream.response(simple_generator())

        async def simple_generator():
            for i in range(10):
                yield SSEEvent(data={"i": i})
                await asyncio.sleep(0.5)

    用法 2 — 队列模式（适合外部生产数据）：
        stream = SSEStream()
        # 在另一个 task 里：
        await stream.send(SSEEvent(data="hello"))
        await stream.close()
        # 在路由里：
        return stream.response()

    用法 3 — 进度回调模式：
        async def long_task(on_progress):
            for i in range(100):
                on_progress(i, 100, f"step {i}")
                await asyncio.sleep(0.1)

        @app.get("/progress")
        async def progress():
            return SSEStream.progress(long_task, event_name="progress")
    """

    def __init__(self, heartbeat_interval: float = 15.0) -> None:
        """
        Args:
            heartbeat_interval: 心跳间隔秒数，默认 15 秒。0 表示不发送心跳。
        """
        self._queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue()
        self._heartbeat_interval = heartbeat_interval
        self._closed = False

    # —— 写入端 API ——
    async def send(self, event: SSEEvent) -> None:
        """发送一个事件"""
        if self._closed:
            logger.warning("SSEStream is closed, dropping event")
            return
        await self._queue.put(event)

    async def send_data(self, data: Any, event: str | None = None) -> None:
        """快捷发送纯数据事件"""
        await self.send(SSEEvent(data=data, event=event))

    async def send_error(self, message: str, code: str = "error") -> None:
        """发送错误事件"""
        await self.send(SSEEvent(data={"code": code, "message": message}, event="error"))

    async def send_done(self, data: Any | None = None) -> None:
        """发送完成事件并关闭流"""
        if data is not None:
            await self.send(SSEEvent(data=data, event="done"))
        else:
            await self.send(SSEEvent(data="[DONE]", event="done"))
        await self.close()

    async def close(self) -> None:
        """关闭流"""
        if not self._closed:
            self._closed = True
            await self._queue.put(None)

    # —— 读取端（生成器） ——
    async def _generator(self) -> AsyncGenerator[str, None]:
        """内部生成器，产出 SSE 格式文本"""
        # 第一条消息：保活测试
        yield SSEEvent(data="connected", event="open").format()

        try:
            while True:
                try:
                    # 带超时等待，用于定期发送心跳
                    event = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=self._heartbeat_interval if self._heartbeat_interval > 0 else None,
                    )
                    if event is None:  # 关闭信号
                        break
                    yield event.format()
                except TimeoutError:
                    # 心跳
                    yield SSEEvent(data="ping", event="heartbeat").format()
        except asyncio.CancelledError:
            logger.debug("SSEStream client disconnected")
            raise
        finally:
            self._closed = True

    # —— FastAPI 响应工厂 ——
    def response(self, **kwargs) -> StreamingResponse:
        """
        生成 FastAPI StreamingResponse。

        额外 kwargs 会传给 StreamingResponse（如 headers、status_code 等）。
        """
        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        headers.update(kwargs.pop("headers", {}))
        return StreamingResponse(
            self._generator(),
            media_type="text/event-stream",
            headers=headers,
            **kwargs,
        )

    # —— 便捷静态方法 ——
    @staticmethod
    def from_generator(
        gen: AsyncGenerator[SSEEvent | Any, None],
        *,
        event_name: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> StreamingResponse:
        """
        从异步生成器创建 SSE 响应。

        如果生成器产出的不是 SSEEvent，会自动包装为指定 event_name 的事件。
        """
        stream = SSEStream(heartbeat_interval=heartbeat_interval)

        async def _wrap():
            try:
                async for item in gen:
                    if isinstance(item, SSEEvent):
                        await stream.send(item)
                    else:
                        await stream.send(SSEEvent(data=item, event=event_name))
                await stream.send_done()
            except Exception as exc:
                logger.exception("SSE generator error")
                await stream.send_error(str(exc))
                await stream.close()

        asyncio.create_task(_wrap())
        return stream.response()

    @staticmethod
    def progress(
        task_fn: Callable,
        *,
        event_name: str = "progress",
        heartbeat_interval: float = 15.0,
        **task_kwargs,
    ) -> StreamingResponse:
        """
        进度回调模式：将一个接受 on_progress 回调的长任务包装为 SSE 流。

        task_fn 的第一个参数应该是 on_progress 回调函数，
        on_progress(current, total, message=None)。

        Args:
            task_fn: 长任务函数 (async)，签名为 async def task(on_progress, **kwargs)
            event_name: 进度事件名
            heartbeat_interval: 心跳间隔
            **task_kwargs: 传给 task_fn 的额外参数
        """
        stream = SSEStream(heartbeat_interval=heartbeat_interval)
        start_time = time.time()

        def on_progress(current: int | float, total: int | float, message: str | None = None):
            percent = round(current / total * 100, 2) if total > 0 else 0
            elapsed = round(time.time() - start_time, 2)
            data = {
                "current": current,
                "total": total,
                "percent": percent,
                "elapsed": elapsed,
                "message": message,
            }
            asyncio.create_task(stream.send(SSEEvent(data=data, event=event_name)))

        async def _run():
            try:
                result = await task_fn(on_progress, **task_kwargs)
                await stream.send_done({"result": result} if result is not None else None)
            except Exception as exc:
                logger.exception("Progress task error")
                await stream.send_error(str(exc))
                await stream.close()

        asyncio.create_task(_run())
        return stream.response()
