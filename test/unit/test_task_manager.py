# @Description : 里程碑 8 单元测试（TaskManager 注册/调度/触发/SSE 进度 + tasks 路由）
from __future__ import annotations

import asyncio
import sys

import httpx
from fastapi import FastAPI

from src.faster.routers.tasks import tasks_router
from src.my_tools.schedule_tasks import TaskManager, TaskRun
from src.my_tools.schedule_tasks.examples import register_example_tasks


async def _consume(stream, timeout: float = 5.0) -> str:
    """消费一条 SSEStream 的全部事件文本（晚订阅回放）"""
    chunks: list[str] = []
    try:
        async with asyncio.timeout(timeout):
            async for chunk in stream._generator():
                chunks.append(chunk)
                if "event: done" in chunk or "event: error" in chunk:
                    break
    except TimeoutError:
        pass
    return "".join(chunks)


async def _wait_done(manager: TaskManager, run_id: str, timeout: float = 5.0) -> TaskRun:
    run = manager.get_run(run_id)
    try:
        async with asyncio.timeout(timeout):
            while run.status in ("pending", "running"):
                await asyncio.sleep(0.02)
                run = manager.get_run(run_id)
    except TimeoutError:
        pass
    return run


# ---------------------------------------------------------------------------
#  注册与列表
# ---------------------------------------------------------------------------
async def test_register_and_list():
    print("\nTest 1: 任务注册（重复 / 非异步拒绝）与列表元数据")
    manager = TaskManager()
    register_example_tasks(manager)
    names = {t["name"] for t in manager.list_tasks()}
    assert {"progress_demo", "heartbeat", "quarterly_task"} <= names

    heartbeat = next(t for t in manager.list_tasks() if t["name"] == "heartbeat")
    assert heartbeat["scheduled"] is True  # interval 调度已附加
    quarterly = next(t for t in manager.list_tasks() if t["name"] == "quarterly_task")
    assert quarterly["scheduled"] is True

    try:
        manager.register(lambda: None, name="bad_sync")
    except TypeError:
        pass
    else:
        raise AssertionError("非异步函数应被拒绝")
    try:
        manager.register(manager._tasks["heartbeat"].func, name="heartbeat")
    except ValueError:
        pass
    else:
        raise AssertionError("重名注册应被拒绝")
    print("  3 个示例任务注册 + 调度信息 + 重复/非异步拒绝")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  手动触发与状态流转
# ---------------------------------------------------------------------------
async def test_trigger_success():
    print("\nTest 2: 手动触发成功（状态流转 / 进度 / 结果 / 耗时）")
    manager = TaskManager()
    register_example_tasks(manager)
    run = manager.trigger("progress_demo", task_kwargs={"steps": 3, "interval": 0.02})
    assert run.status in ("pending", "running")
    assert run.trigger_type == "manual"

    done = await _wait_done(manager, run.run_id)
    assert done.status == "success"
    assert done.progress == 100.0
    assert done.result == {"steps": 3, "interval": 0.02}
    assert done.duration_ms is not None and done.duration_ms >= 0
    print(f"  run={done.run_id} status={done.status} progress={done.progress} duration={done.duration_ms}ms")
    print("  ✅ 通过")


async def test_trigger_failure():
    print("\nTest 3: 任务失败（status=failed + error 记录）")
    manager = TaskManager()

    async def boom(ctx):
        raise ValueError("task exploded")

    manager.register(boom, name="boom")
    run = manager.trigger("boom")
    done = await _wait_done(manager, run.run_id)
    assert done.status == "failed"
    assert "ValueError: task exploded" in done.error
    print(f"  error={done.error}")
    print("  ✅ 通过")


async def test_sse_progress_stream():
    print("\nTest 4: SSE 进度流（start / progress / done 事件 + 晚订阅回放）")
    manager = TaskManager()
    register_example_tasks(manager)
    run = manager.trigger("progress_demo", task_kwargs={"steps": 3, "interval": 0.02})
    await _wait_done(manager, run.run_id)  # 任务先跑完

    stream = manager.subscribe(run.run_id)  # 之后才订阅 -> 回放历史事件
    assert stream is not None
    text = await _consume(stream)
    assert "event: open" in text
    assert "event: start" in text
    assert text.count("event: progress") == 3
    assert "event: done" in text
    assert '"status": "success"' in text
    assert manager.subscribe("nonexistent") is None
    print(f"  回放 open/start/3xprogress/done，共 {len(text)} 字符")
    print("  ✅ 通过")


async def test_runs_retention():
    print("\nTest 5: 执行记录 LRU 清理（max_runs）")
    manager = TaskManager(max_runs=3)
    register_example_tasks(manager)
    for _ in range(5):
        run = manager.trigger("progress_demo", task_kwargs={"steps": 1, "interval": 0.01})
        await _wait_done(manager, run.run_id)
    assert len(manager._runs) == 3
    assert len(manager.recent_runs(limit=10)) == 3
    assert manager.recent_runs(limit=10)[0]["run_id"] == run.run_id  # 最新的保留
    print("  5 次执行只保留最近 3 条")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  调度（真实 AsyncIOScheduler）
# ---------------------------------------------------------------------------
async def test_scheduler_execution():
    print("\nTest 6: interval 调度真实执行（scheduled 类型 run 记录）")
    manager = TaskManager()

    async def fast_task(ctx):
        return "tick"

    manager.register(fast_task, name="fast_task")
    manager.schedule_interval("fast_task", seconds=0.1)
    manager.start()
    try:
        await asyncio.sleep(0.45)
        scheduled = manager.recent_runs(name="fast_task")
        assert len(scheduled) >= 2
        assert all(r["trigger_type"] == "scheduled" for r in scheduled)
        assert all(r["status"] == "success" for r in scheduled)
    finally:
        manager.close()
    print(f"  0.45s 内调度执行 {len(scheduled)} 次")
    print("  ✅ 通过")


async def test_pause_resume():
    print("\nTest 7: 暂停 / 恢复调度")
    manager = TaskManager()

    async def slow_task(ctx):
        return 1

    manager.register(slow_task, name="slow_task")
    manager.schedule_interval("slow_task", seconds=0.1)
    manager.start()
    try:
        info = next(t for t in manager.list_tasks() if t["name"] == "slow_task")
        assert info["scheduled"] is True and info["paused"] is False and info["next_run_time"] is not None

        manager.pause("slow_task")
        await asyncio.sleep(0.05)
        info = next(t for t in manager.list_tasks() if t["name"] == "slow_task")
        assert info["paused"] is True and info["next_run_time"] is None

        manager.resume("slow_task")
        await asyncio.sleep(0.05)
        info = next(t for t in manager.list_tasks() if t["name"] == "slow_task")
        assert info["paused"] is False and info["next_run_time"] is not None
    finally:
        manager.close()
    print("  paused 标记与 next_run_time 正确翻转")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  路由集成
# ---------------------------------------------------------------------------
def build_app(manager: TaskManager) -> FastAPI:
    app = FastAPI()
    app.include_router(tasks_router)
    app.state.task_manager = manager
    return app


async def test_router_flow():
    print("\nTest 8: 路由集成（列表 / 触发 202 / 状态 / 404 / 暂停恢复）")
    manager = TaskManager()
    register_example_tasks(manager)
    manager.start()
    app = build_app(manager)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/tasks")
            assert r.status_code == 200
            assert any(t["name"] == "progress_demo" for t in r.json()["tasks"])

            r404 = await client.post("/tasks/no_such/trigger")
            assert r404.status_code == 404

            accepted = await client.post("/tasks/progress_demo/trigger", json={"task_kwargs": {"steps": 2, "interval": 0.01}})
            assert accepted.status_code == 202
            run_id = accepted.json()["run"]["run_id"]

            await _wait_done(manager, run_id)
            status_r = await client.get(f"/tasks/runs/{run_id}")
            assert status_r.status_code == 200
            assert status_r.json()["status"] == "success"

            missing = await client.get("/tasks/runs/does-not-exist")
            assert missing.status_code == 404

            pause_r = await client.post("/tasks/heartbeat/pause")
            assert pause_r.status_code == 200
            resume_r = await client.post("/tasks/heartbeat/resume")
            assert resume_r.status_code == 200

            runs_r = await client.get("/tasks/runs")
            assert runs_r.status_code == 200 and len(runs_r.json()["runs"]) >= 1
        print("  list / 202 / success / 404 x2 / pause / resume / runs 全部正确")
        print("  ✅ 通过")
    finally:
        manager.close()


async def test_router_sse():
    print("\nTest 9: SSE 路由（httpx 流式读取进度事件）")
    manager = TaskManager()
    register_example_tasks(manager)
    app = build_app(manager)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            accepted = await client.post("/tasks/progress_demo/trigger", json={"task_kwargs": {"steps": 2, "interval": 0.03}})
            run_id = accepted.json()["run"]["run_id"]

            collected: list[str] = []
            async with asyncio.timeout(5):
                async with client.stream("GET", f"/tasks/runs/{run_id}/events") as resp:
                    assert resp.status_code == 200
                    assert resp.headers["content-type"].startswith("text/event-stream")
                    async for chunk in resp.aiter_text():
                        collected.append(chunk)
                        if "event: done" in chunk:
                            break
            text = "".join(collected)
            assert "event: open" in text
            assert "event: progress" in text
            assert "event: done" in text
            missing = await client.get("/tasks/runs/none/events")
            assert missing.status_code == 404
        print("  SSE 流式收到 open/progress/done")
        print("  ✅ 通过")
    finally:
        manager.close()


async def main():
    print("🚀 后台任务调度模块测试开始")
    try:
        await test_register_and_list()
        await test_trigger_success()
        await test_trigger_failure()
        await test_sse_progress_stream()
        await test_runs_retention()
        await test_scheduler_execution()
        await test_pause_resume()
        await test_router_flow()
        await test_router_sse()
        print("\n" + "=" * 60)
        print("🎉 所有后台任务调度模块测试通过！（9 项）")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
