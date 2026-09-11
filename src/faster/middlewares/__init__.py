# -*- coding: utf-8 -*-
# @Description : 请求上下文中间件（请求 ID 透传 + 结构化访问日志）
from __future__ import annotations

import time
import uuid

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

__all__ = ("RequestContextMiddleware",)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    每个请求：
    - 生成或复用（上游传入的）X-Request-ID
    - request.state.request_id 可在业务代码中取用
    - 响应头回写 X-Request-ID
    - 访问日志：method path status 耗时（loguru contextualize 让请求期日志自动带 request_id）
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id

        start = time.perf_counter()
        with logger.contextualize(request_id=request_id):
            try:
                response = await call_next(request)
            except Exception:
                logger.exception(f"{request.method} {request.url.path} -> 500")
                raise
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

        response.headers["X-Request-ID"] = request_id
        logger.bind(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
        ).info(f"{request.method} {request.url.path} -> {response.status_code} [{elapsed_ms}ms]")
        return response
