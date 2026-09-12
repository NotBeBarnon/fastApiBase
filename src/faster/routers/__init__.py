from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import ORJSONResponse
from loguru import logger
from pydantic import BaseModel

from ...settings import HTTP_BASE_URL
from .kafka import kafka_router
from .llm import llm_router
from .mcp import mcp_router
from .monitor import monitor_router
from .resource.routers import resource_router
from .security import security_router
from .sse import sse_router
from .tasks import tasks_router
from .users.routers import user_router

__all__ = ("all_router",)

all_router = APIRouter(prefix=HTTP_BASE_URL)
all_router.include_router(user_router)
all_router.include_router(resource_router)
all_router.include_router(mcp_router)
all_router.include_router(sse_router)
all_router.include_router(llm_router)
all_router.include_router(monitor_router)
all_router.include_router(security_router)
all_router.include_router(kafka_router)
all_router.include_router(tasks_router)


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
