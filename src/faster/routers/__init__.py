# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import ORJSONResponse
from loguru import logger
from pydantic import BaseModel

from ...settings import HTTP_BASE_URL
from .resource.routers import resource_router
from .users.routers import user_router

__all__ = ("all_router",)

all_router = APIRouter(prefix=HTTP_BASE_URL)
all_router.include_router(user_router)
all_router.include_router(resource_router)


class FastAPIStatus(BaseModel):
    message: str = "FastAPI success!"


@all_router.get(
    "/check",
    summary="验活",
    response_class=ORJSONResponse,
    response_model=FastAPIStatus,
    response_description="验活成功响应",
)
def home(request: Request) -> FastAPIStatus:
    logger.debug(f"Request from [{request.client}]")
    return FastAPIStatus()
