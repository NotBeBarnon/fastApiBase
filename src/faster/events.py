# -*- coding: utf-8 -*-
# @Description : FastAPI 生命周期（lifespan）
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from loguru import logger
from tortoise import Tortoise

from ..my_tools.redis_tools.clients import RedisSentinelClient
from ..my_tools.schedule_tasks.scheduleUtils import quarterly_task
from ..settings import DATABASE_CONFIG, DEFAULT_TIMEZONE, REDIS_CONFIG

__all__ = ("lifespan",)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动与关闭生命周期，所有资源挂在 app.state 上"""

    logger.info("Startup: initializing resources")

    # 1. Tortoise ORM
    await Tortoise.init(config=DATABASE_CONFIG)
    logger.info(f"Tortoise-ORM started: {Tortoise.apps}")

    # 2. Redis 哨兵客户端
    redis_client = RedisSentinelClient(
        sentinels=REDIS_CONFIG["sentinels"]["service"],
        service_name=REDIS_CONFIG["sentinels"]["service_name"],
        db=REDIS_CONFIG["db"],
        user=REDIS_CONFIG["user"],
        password=REDIS_CONFIG["password"],
        retry_interval=REDIS_CONFIG["retry_interval"],
    )
    redis_client.start()
    with contextlib.suppress(asyncio.TimeoutError):
        async with asyncio.timeout(3):
            await redis_client.wait_connect()
    app.state.redis = redis_client

    # 3. 定时任务
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        quarterly_task,
        CronTrigger(month="1,4,7,10", day=1, hour=0, minute=0, second=0, timezone=DEFAULT_TIMEZONE),
    )
    scheduler.start()
    app.state.scheduler = scheduler
    logger.debug("Scheduler started")

    try:
        yield
    finally:
        logger.info("Shutdown: releasing resources")
        scheduler.shutdown(wait=False)
        redis_client.stop()
        await Tortoise.close_connections()
        logger.info("Tortoise-ORM shutdown")
