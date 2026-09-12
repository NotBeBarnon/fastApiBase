# @Description : 后台任务管理器（注册中心 + 调度 + 状态跟踪 + SSE 进度流）
from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from src.my_tools.sse_tools import SSEEvent, SSEStream

__all__ = ("TaskContext", "TaskManager", "TaskRun")

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


@dataclass
class TaskRun:
    """一次任务执行的记录（手动触发或调度触发）"""

    run_id: str
    task_name: str
    trigger_type: str  # manual / scheduled
    status: str = STATUS_PENDING
    progress: float = 0.0  # 0 - 100
    message: str = ""
    result: Any = None
    error: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    duration_ms: float | None = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "task_name": self.task_name,
            "trigger_type": self.trigger_type,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class TaskContext:
    """注入给任务函数的执行上下文：任务内通过 ctx.report 上报进度（同时推 SSE）"""

    def __init__(self, run: TaskRun, stream: SSEStream) -> None:
        self._run = run
        self._stream = stream

    async def report(self, percent: float, message: str = "") -> None:
        self._run.progress = round(float(percent), 2)
        self._run.message = message
        await self._stream.send(SSEEvent(data={"percent": self._run.progress, "message": message}, event="progress"))


@dataclass
class _TaskDef:
    func: Callable
    name: str
    description: str = ""


class TaskManager:
    """
    后台任务管理器，封装 AsyncIOScheduler：

    - register：注册任务（支持装饰器），任务函数第一个参数名为 ctx 时自动注入 TaskContext
    - schedule_interval / schedule_cron：附加调度（自动重连入 jobstore），pause / resume 动态控制
    - trigger：手动后台触发（立即返回 run_id，可轮询 /tasks/runs/{id} 或 SSE 订阅进度）
    - 每次（含调度）执行都记录 TaskRun（状态流转 / 进度 / 结果 / 耗时），保留最近 max_runs 条
    """

    def __init__(self, max_runs: int = 200) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._tasks: dict[str, _TaskDef] = {}
        self._runs: dict[str, TaskRun] = {}
        self._streams: dict[str, SSEStream] = {}
        self._max_runs = max_runs

    # ------------------------------------------------------------------
    #  注册
    # ------------------------------------------------------------------
    def register(self, func: Callable | None = None, *, name: str | None = None, description: str = ""):
        """注册任务，可直接调用 register(fn, name=...) 也可作装饰器 @register(name=...)"""

        def _wrap(fn: Callable) -> Callable:
            task_name = name or fn.__name__
            if task_name in self._tasks:
                raise ValueError(f"Task already registered: {task_name}")
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"Task must be an async function: {task_name}")
            self._tasks[task_name] = _TaskDef(func=fn, name=task_name, description=description or (fn.__doc__ or ""))
            logger.debug(f"TaskManager: registered <{task_name}>")
            return fn

        return _wrap(func) if func is not None else _wrap

    # ------------------------------------------------------------------
    #  调度控制
    # ------------------------------------------------------------------
    def schedule_interval(self, name: str, seconds: int, *, task_kwargs: dict | None = None) -> None:
        """按固定间隔调度已注册任务"""
        task = self._require_task(name)
        self._scheduler.add_job(
            self._scheduled_exec,
            IntervalTrigger(seconds=seconds),
            kwargs={"name": name, "task_kwargs": task_kwargs or {}},
            id=name,
            name=task.description or name,
            replace_existing=True,
        )

    def schedule_cron(self, name: str, task_kwargs: dict | None = None, **cron) -> None:
        """按 cron 表达式字段调度已注册任务（如 month='1,4,7,10', day=1, hour=0）"""
        task = self._require_task(name)
        cron.setdefault("timezone", "UTC")
        self._scheduler.add_job(
            self._scheduled_exec,
            CronTrigger(**cron),
            kwargs={"name": name, "task_kwargs": task_kwargs or {}},
            id=name,
            name=task.description or name,
            replace_existing=True,
        )

    def pause(self, name: str) -> None:
        """暂停调度（不影响手动 trigger）"""
        self._require_task(name)
        self._scheduler.pause_job(name)

    def resume(self, name: str) -> None:
        self._require_task(name)
        self._scheduler.resume_job(name)

    def unschedule(self, name: str) -> None:
        """移除调度（任务定义保留，仍可手动 trigger）"""
        self._require_task(name)
        self._scheduler.remove_job(name)

    # ------------------------------------------------------------------
    #  触发与执行
    # ------------------------------------------------------------------
    def trigger(self, name: str, *, task_kwargs: dict | None = None) -> TaskRun:
        """手动后台触发（立即返回 TaskRun，不等待完成）"""
        task = self._require_task(name)
        run = self._new_run(name, "manual")
        asyncio.create_task(self._execute(task, run, task_kwargs or {}))
        return run

    async def _scheduled_exec(self, name: str, task_kwargs: dict | None = None) -> None:
        """调度器回调入口：创建 run 记录并执行"""
        task = self._require_task(name)
        run = self._new_run(name, "scheduled")
        await self._execute(task, run, task_kwargs or {})

    async def _execute(self, task: _TaskDef, run: TaskRun, task_kwargs: dict) -> None:
        stream = self._streams[run.run_id]
        run.status = STATUS_RUNNING
        run.started_at = time.time()
        await stream.send(SSEEvent(data=run.to_dict(), event="start"))
        try:
            params = list(inspect.signature(task.func).parameters)
            if params and params[0] == "ctx":
                result = await task.func(TaskContext(run, stream), **task_kwargs)
            else:
                result = await task.func(**task_kwargs)
        except Exception as exc:
            run.status = STATUS_FAILED
            run.error = f"{exc.__class__.__name__}: {exc}"
            run.finished_at = time.time()
            run.duration_ms = round((run.finished_at - (run.started_at or run.finished_at)) * 1000, 2)
            logger.bind(task=task.name, run_id=run.run_id).warning(f"task failed: {run.error}")
            await stream.send_error(run.error)
            await stream.close()
            return

        run.status = STATUS_SUCCESS
        run.progress = 100.0
        run.result = result
        run.finished_at = time.time()
        run.duration_ms = round((run.finished_at - (run.started_at or run.finished_at)) * 1000, 2)
        logger.bind(task=task.name, run_id=run.run_id, duration_ms=run.duration_ms).info(f"task done: {task.name}")
        await stream.send_done(run.to_dict())

    # ------------------------------------------------------------------
    #  查询
    # ------------------------------------------------------------------
    def list_tasks(self) -> list[dict]:
        """全部注册任务 + 调度信息（未 start 时读取 pending jobs）+ 最近一次执行摘要"""
        pending_jobs = {} if self._scheduler.running else {j.id: j for j in self._scheduler.get_jobs(pending=True)}
        result = []
        for name, task in self._tasks.items():
            if self._scheduler.running:
                job = self._scheduler.get_job(name)
                pending = False
            else:
                job = pending_jobs.get(name)
                pending = job is not None
            last = self._last_run(name)
            next_run = getattr(job, "next_run_time", None) if job is not None else None
            result.append(
                {
                    "name": name,
                    "description": task.description.strip().split("\n")[0],
                    "scheduled": job is not None,
                    "next_run_time": next_run.isoformat() if next_run else None,
                    "paused": job is not None and not pending and next_run is None,
                    "last_run": {"status": last.status, "duration_ms": last.duration_ms} if last else None,
                }
            )
        return result

    def get_run(self, run_id: str) -> TaskRun | None:
        return self._runs.get(run_id)

    def recent_runs(self, name: str | None = None, limit: int = 20) -> list[dict]:
        runs = [r for r in reversed(self._runs.values()) if name is None or r.task_name == name]
        return [r.to_dict() for r in runs[:limit]]

    def subscribe(self, run_id: str) -> SSEStream | None:
        """获取某次执行的 SSE 进度流（晚订阅也能回放队列中的历史事件）"""
        if run_id not in self._runs:
            return None
        return self._streams[run_id]

    # ------------------------------------------------------------------
    #  生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        self._scheduler.start()

    def close(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        for stream in self._streams.values():
            asyncio.ensure_future(stream.close())

    # ------------------------------------------------------------------
    #  内部
    # ------------------------------------------------------------------
    def _require_task(self, name: str) -> _TaskDef:
        task = self._tasks.get(name)
        if task is None:
            raise KeyError(f"Task not found: {name}")
        return task

    def _new_run(self, name: str, trigger_type: str) -> TaskRun:
        run = TaskRun(run_id=uuid.uuid4().hex[:12], task_name=name, trigger_type=trigger_type)
        self._runs[run.run_id] = run
        self._streams[run.run_id] = SSEStream()
        # 超量清理：删最老 run 与对应 stream
        while len(self._runs) > self._max_runs:
            oldest = next(iter(self._runs))
            self._runs.pop(oldest, None)
            self._streams.pop(oldest, None)
        return run

    def _last_run(self, name: str) -> TaskRun | None:
        for run in reversed(self._runs.values()):
            if run.task_name == name:
                return run
        return None
