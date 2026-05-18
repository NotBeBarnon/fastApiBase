# -*- coding: utf-8 -*-
# @Description : 定时任务函数。Scheduler 实例由 events.lifespan 创建并注入 app.state。
from __future__ import annotations

from loguru import logger


async def quarterly_task() -> None:
    """每季度首日 0 点执行的示例任务"""
    logger.info("quarterly_task triggered")
