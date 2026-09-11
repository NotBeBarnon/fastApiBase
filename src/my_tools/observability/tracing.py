# -*- coding: utf-8 -*-
# @Description : LLM 调用链路追踪（结构化日志 + trace_id 透传）
from __future__ import annotations

import contextvars
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from loguru import logger

__all__ = ("trace_llm_call", "get_trace_id", "new_trace_id")

_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("llm_trace_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id() -> str | None:
    return _trace_id_var.get()


@asynccontextmanager
async def trace_llm_call(
    operation: str,
    *,
    provider: str = "",
    model: str = "",
    trace_id: str | None = None,
    **extra: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    LLM 调用追踪上下文。

    - 生成/透传 trace_id，写入 contextvar 供嵌套调用复用
    - 进入时记录开始事件，退出时记录耗时与成败
    - 通过 context 字典回传 latency_ms / error，便于调用方采集指标

    用法：
        async with trace_llm_call("chat", provider="deepseek") as ctx:
            resp = await do_call()
            ctx["tokens"] = resp.usage.total_tokens
    """
    tid = trace_id or _trace_id_var.get() or new_trace_id()
    token = _trace_id_var.set(tid)
    context: dict[str, Any] = {"trace_id": tid, "latency_ms": 0.0}
    start = time.time()
    logger.bind(trace_id=tid, operation=operation, provider=provider, model=model, **extra).info(
        f"llm call start: {operation}"
    )
    try:
        yield context
    except Exception as exc:
        context["error"] = f"{exc.__class__.__name__}: {exc}"
        logger.bind(
            trace_id=tid,
            operation=operation,
            provider=provider,
            model=model,
            latency_ms=round((time.time() - start) * 1000, 2),
            **extra,
        ).warning(f"llm call failed: {operation} - {context['error']}")
        raise
    finally:
        context["latency_ms"] = round((time.time() - start) * 1000, 2)
        logger.bind(
            trace_id=tid,
            operation=operation,
            provider=provider,
            model=model,
            latency_ms=context["latency_ms"],
            tokens=context.get("tokens"),
            **extra,
        ).info(f"llm call done: {operation}")
        _trace_id_var.reset(token)
