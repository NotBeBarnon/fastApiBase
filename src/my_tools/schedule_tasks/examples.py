# @Description : 后台任务示例（进度上报 / 心跳 / 季度任务），业务任务按此模式追加
from __future__ import annotations

import asyncio

from loguru import logger

from .manager import TaskManager
from .scheduleUtils import quarterly_task

__all__ = ("heartbeat_task", "progress_demo_task", "register_example_tasks",)


async def progress_demo_task(ctx, steps: int = 5, interval: float = 0.3) -> dict:
    """演示进度上报的任务：分 steps 步推进并上报百分比（SSE 可订阅）"""
    for i in range(1, steps + 1):
        await asyncio.sleep(interval)
        await ctx.report(i / steps * 100, f"step {i}/{steps}")
    return {"steps": steps, "interval": interval}


async def heartbeat_task() -> None:
    """每 30 秒的心跳任务（演示 interval 调度）"""
    logger.debug("heartbeat_task triggered")


def register_example_tasks(manager: TaskManager) -> None:
    """注册内置示例任务与默认调度（新增业务任务在此追加）"""
    manager.register(progress_demo_task, name="progress_demo", description="演示进度上报的任务（可 SSE 订阅）")
    manager.register(heartbeat_task, name="heartbeat", description="心跳任务（每 30 秒）")
    manager.register(quarterly_task, name="quarterly_task", description="每季度首日 0 点执行的示例任务")

    manager.schedule_interval("heartbeat", seconds=30)
    manager.schedule_cron("quarterly_task", month="1,4,7,10", day=1, hour=0, minute=0, second=0)
