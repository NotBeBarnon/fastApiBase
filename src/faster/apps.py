# -*- coding: utf-8 -*-
# @Description : FastAPI 应用实例
from __future__ import annotations

from fastapi import FastAPI

from ..settings import HTTP_BASE_URL
from ..version import VERSION
from .events import lifespan
from .routers import all_router

__all__ = ("fast_app",)


fast_app = FastAPI(
    title="FastSample",
    description="FastAPI 示例项目",
    version=VERSION,
    openapi_url=f"{HTTP_BASE_URL}/openapi.json",
    docs_url=f"{HTTP_BASE_URL}/docs",
    redoc_url=f"{HTTP_BASE_URL}/redoc",
    lifespan=lifespan,
)

fast_app.include_router(all_router)
