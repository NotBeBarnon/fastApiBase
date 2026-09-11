# @Description : SSE 流式响应工具测试
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.my_tools.sse_tools import SSEEvent, SSEStream
from src.my_tools.sse_tools.llm_streamer import LLMChunk, LLMStreamer, LLMStreamFormat


def test_sse_event_format():
    """测试 SSEEvent 格式化"""
    print("Test 1: SSEEvent 格式化")

    # 简单字符串
    e = SSEEvent(data="hello")
    out = e.format()
    assert "data: hello" in out
    assert out.endswith("\n\n")
    print("  - 字符串 data ✅")

    # dict 数据
    e = SSEEvent(data={"key": "值", "num": 42})
    out = e.format()
    assert "data: " in out
    # 应该是合法 JSON
    data_line = [line for line in out.split("\n") if line.startswith("data: ")][0]
    obj = json.loads(data_line[6:])
    assert obj["key"] == "值"
    assert obj["num"] == 42
    print("  - dict data ✅")

    # 带 event / id / retry
    e = SSEEvent(data="test", event="progress", id="msg-1", retry=3000)
    out = e.format()
    assert "event: progress" in out
    assert "id: msg-1" in out
    assert "retry: 3000" in out
    print("  - 完整字段 ✅")

    # 多行 data
    e = SSEEvent(data="line1\nline2\nline3")
    out = e.format()
    assert out.count("data: ") == 3
    print("  - 多行 data ✅")


async def test_sse_stream_queue():
    """测试 SSEStream 队列模式"""
    print("\nTest 2: SSEStream 队列模式")

    stream = SSEStream(heartbeat_interval=0)  # 关掉心跳

    async def producer():
        await asyncio.sleep(0.01)
        await stream.send_data("first", event="msg")
        await asyncio.sleep(0.01)
        await stream.send_data({"num": 2}, event="msg")
        await asyncio.sleep(0.01)
        await stream.send_done()

    asyncio.create_task(producer())

    events: list[str] = []
    async for line in stream._generator():
        events.append(line)

    # 应该有 open + 2 条消息 + done
    assert len(events) >= 3
    assert "event: open" in events[0]
    assert "first" in events[1]
    print("  ✅ 通过")


async def test_sse_from_generator():
    """测试 from_generator 便捷方法"""
    print("\nTest 3: from_generator")

    async def gen():
        for i in range(5):
            yield {"i": i}
            await asyncio.sleep(0.001)

    resp = SSEStream.from_generator(gen(), event_name="data", heartbeat_interval=0)
    assert resp.media_type == "text/event-stream"
    print("  - 返回 StreamingResponse ✅")

    # 收集数据
    events: list[str] = []
    async for chunk in resp.body_iterator:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        events.append(chunk)

    full = "".join(events)
    assert "event: open" in full
    assert '"i": 0' in full
    assert "event: done" in full
    print("  - 内容正确 ✅")


async def test_sse_progress():
    """测试进度回调模式"""
    print("\nTest 4: progress 模式")

    async def task(on_progress, total: int):
        for i in range(total):
            on_progress(i + 1, total, f"step {i+1}")
            await asyncio.sleep(0.001)
        return "done"

    resp = SSEStream.progress(task, total=3, heartbeat_interval=0)
    assert resp.media_type == "text/event-stream"

    events: list[str] = []
    async for chunk in resp.body_iterator:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        events.append(chunk)

    full = "".join(events)
    assert "event: progress" in full
    assert "percent" in full
    assert '"result": "done"' in full
    print("  ✅ 通过")


async def test_llm_streamer_text():
    """测试 LLMStreamer 文本生成器模式"""
    print("\nTest 5: LLMStreamer 文本模式")

    streamer = LLMStreamer(fmt=LLMStreamFormat.PLAIN)

    async def text_gen():
        for word in ["你", "好", "，", "世", "界"]:
            yield word
            await asyncio.sleep(0.001)

    chunks = []
    async for chunk in streamer.from_text_generator(text_gen()):
        chunks.append(chunk)

    assert len(chunks) == 5
    assert streamer.full_content == "你好，世界"
    assert streamer.finish_reason == "stop"
    print("  ✅ 通过")


async def test_llm_streamer_openai_format():
    """测试 LLMStreamer OpenAI 格式输出"""
    print("\nTest 6: LLMStreamer OpenAI 格式输出")

    streamer = LLMStreamer(fmt=LLMStreamFormat.OPENAI)

    async def text_gen():
        for word in ["Hello", " world"]:
            yield word
            await asyncio.sleep(0.001)

    events: list[SSEEvent] = []
    async for event in streamer.generate(text_gen(), source="text"):
        events.append(event)

    # 应该有 delta 事件 + done 事件
    assert len(events) >= 2
    assert any("Hello" in e.format() for e in events)
    assert any(e.event == "done" for e in events)
    print("  ✅ 通过")


async def test_llm_streamer_event_format():
    """测试 LLMStreamer EVENT 格式输出"""
    print("\nTest 7: LLMStreamer EVENT 格式输出")

    streamer = LLMStreamer(fmt=LLMStreamFormat.EVENT)

    async def text_gen():
        yield "你好"
        await asyncio.sleep(0.001)

    events: list[SSEEvent] = []
    async for event in streamer.generate(text_gen(), source="text"):
        events.append(event)

    assert any(e.event == "delta" for e in events)
    assert any(e.event == "done" for e in events)
    print("  ✅ 通过")


async def test_llm_chunk():
    """测试 LLMChunk 数据结构"""
    print("\nTest 8: LLMChunk 数据结构")

    chunk = LLMChunk(
        content="hello",
        role="assistant",
        tool_calls=[{"id": "1", "function": {"name": "test"}}],
        finish_reason="stop",
        model="gpt-4",
    )
    assert chunk.content == "hello"
    assert chunk.role == "assistant"
    assert len(chunk.tool_calls) == 1
    assert chunk.finish_reason == "stop"
    print("  ✅ 通过")


async def main():
    print("\n🚀 SSE 工具测试开始\n")
    try:
        test_sse_event_format()
        await test_sse_stream_queue()
        await test_sse_from_generator()
        await test_sse_progress()
        await test_llm_streamer_text()
        await test_llm_streamer_openai_format()
        await test_llm_streamer_event_format()
        await test_llm_chunk()
        print("\n" + "=" * 60)
        print("🎉 所有 SSE 测试通过！")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
