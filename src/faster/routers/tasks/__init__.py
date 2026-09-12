# @Description : 后台任务路由（列表 / 手动触发 / 状态查询 / SSE 进度订阅 / 调度控制）
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

__all__ = ("tasks_router",)

tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])


class TriggerRequest(BaseModel):
    task_kwargs: dict = Field(default_factory=dict, description="传给任务函数的关键字参数")


def _require_manager(request: Request):
    manager = getattr(request.app.state, "task_manager", None)
    if manager is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "TaskManager not mounted")
    return manager


def _require_task_or_404(manager, name: str):
    try:
        manager._require_task(name)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Task not found: {name}") from exc


@tasks_router.get("", summary="全部注册任务与调度状态")
async def list_tasks(request: Request) -> dict:
    manager = _require_manager(request)
    return {"tasks": manager.list_tasks()}


@tasks_router.post("/{name}/trigger", summary="手动触发一次后台执行（返回 run_id）", status_code=status.HTTP_202_ACCEPTED)
async def trigger_task(name: str, request: Request, body: TriggerRequest | None = None) -> dict:
    manager = _require_manager(request)
    _require_task_or_404(manager, name)
    run = manager.trigger(name, task_kwargs=body.task_kwargs if body else None)
    return {"message": "accepted", "run": run.to_dict()}


@tasks_router.get("/runs/{run_id}", summary="查询单次执行状态")
async def get_run(run_id: str, request: Request) -> dict:
    manager = _require_manager(request)
    run = manager.get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run not found: {run_id}")
    return run.to_dict()


@tasks_router.get("/runs/{run_id}/events", summary="SSE 订阅执行进度（晚订阅可回放历史事件）")
async def subscribe_run(run_id: str, request: Request):
    manager = _require_manager(request)
    stream = manager.subscribe(run_id)
    if stream is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run not found: {run_id}")
    return stream.response()


@tasks_router.post("/{name}/pause", summary="暂停该任务的调度（手动触发不受影响）")
async def pause_task(name: str, request: Request) -> dict:
    manager = _require_manager(request)
    _require_task_or_404(manager, name)
    manager.pause(name)
    return {"message": f"scheduled job '{name}' paused"}


@tasks_router.post("/{name}/resume", summary="恢复该任务的调度")
async def resume_task(name: str, request: Request) -> dict:
    manager = _require_manager(request)
    _require_task_or_404(manager, name)
    manager.resume(name)
    return {"message": f"scheduled job '{name}' resumed"}


@tasks_router.get("/runs", summary="最近的执行记录")
async def recent_runs(request: Request, name: str | None = None, limit: int = 20) -> dict:
    manager = _require_manager(request)
    return {"runs": manager.recent_runs(name=name, limit=limit)}
