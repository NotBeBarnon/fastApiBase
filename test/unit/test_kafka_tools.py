# @Description : 里程碑 7 单元测试（Kafka 事件发布 / 示例回调 / 演示路由）
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from src.faster.routers.kafka import DEFAULT_TOPIC, kafka_router
from src.my_tools.kafka_tools import EventPublisher
from src.my_tools.kafka_tools.callbacks import BaseTopicCallSingle
from src.my_tools.kafka_tools.examples import EchoTopicCall, get_consumer_callbacks
from src.settings import MQ_CONFIG


class _FakeRawProducer:
    def __init__(self, exc: Exception | None = None):
        self.sent: list[tuple] = []
        self._exc = exc

    async def send_and_wait(self, topic, value=None, key=None):
        if self._exc is not None:
            raise self._exc
        self.sent.append((topic, value, key))
        return SimpleNamespace(partition=0, offset=len(self.sent) - 1)


class _FakeProducerClient:
    """模拟 KafkaProducerClient 的上下文协议（__enter__ 返回底层 producer）"""

    def __init__(self, raw: _FakeRawProducer | None):
        self._raw = raw

    def __enter__(self):
        return self._raw

    def __exit__(self, *args):
        return False


def build_app(publisher) -> FastAPI:
    app = FastAPI()
    app.include_router(kafka_router)
    app.state.kafka_publisher = publisher
    return app


# ---------------------------------------------------------------------------
#  EventPublisher
# ---------------------------------------------------------------------------
async def test_publisher_success():
    print("\nTest 1: 发布成功（JSON 序列化 + key 编码 + 计数）")
    raw = _FakeRawProducer()
    publisher = EventPublisher(_FakeProducerClient(raw))

    result = await publisher.publish("ORDERS", {"amount": 100, "note": "你好"}, key="user-1")
    assert result == {"topic": "ORDERS", "partition": 0, "offset": 0}

    topic, value, key = raw.sent[0]
    assert topic == "ORDERS"
    assert value == json.dumps({"amount": 100, "note": "你好"}, ensure_ascii=False).encode("utf-8")
    assert key == b"user-1"
    assert publisher.published == 1 and publisher.failed == 0

    # bytes payload 原样透传
    await publisher.publish("ORDERS", b"raw-bytes")
    assert raw.sent[1][1] == b"raw-bytes"
    print(f"  dict->JSON bytes / bytes 透传 / 计数 published={publisher.published}")
    print("  ✅ 通过")


async def test_publisher_not_connected():
    print("\nTest 2: producer 未连接 -> RuntimeError + failed 计数")
    publisher = EventPublisher(_FakeProducerClient(None))
    try:
        await publisher.publish("T", {"x": 1})
    except RuntimeError as exc:
        assert "not connected" in str(exc)
    else:
        raise AssertionError("未连接时应抛 RuntimeError")
    assert publisher.failed == 1 and publisher.published == 0
    print("  ✅ 通过")


async def test_publisher_send_error():
    print("\nTest 3: 发送异常透传 + failed 计数")
    publisher = EventPublisher(_FakeProducerClient(_FakeRawProducer(exc=ConnectionError("broker down"))))
    try:
        await publisher.publish("T", {"x": 1})
    except ConnectionError:
        pass
    else:
        raise AssertionError("异常应透传")
    stats = publisher.stats()
    assert stats == {"published": 0, "failed": 1, "uptime_seconds": stats["uptime_seconds"]}
    print(f"  stats={stats}")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  示例回调
# ---------------------------------------------------------------------------
async def test_echo_callback():
    print("\nTest 4: EchoTopicCall 回调（JSON / 非 JSON payload + 计数）")
    before = EchoTopicCall.handled
    call = EchoTopicCall()  # 单例
    assert isinstance(call, BaseTopicCallSingle)
    assert call.topic == str(MQ_CONFIG["topics"]["consumer"]["fast_sample"])

    await call.callback(SimpleNamespace(topic=call.topic, offset=1, value=b'{"a": 1}'))
    await call.callback(SimpleNamespace(topic=call.topic, offset=2, value=b"not-json"))
    assert EchoTopicCall.handled == before + 2
    print(f"  topic={call.topic} handled={EchoTopicCall.handled}")
    print("  ✅ 通过")


def test_get_consumer_callbacks():
    print("\nTest 5: 回调注册列表")
    callbacks = get_consumer_callbacks()
    assert len(callbacks) >= 1
    assert all(issubclass(c, BaseTopicCallSingle) for c in callbacks)
    print(f"  {len(callbacks)} 个回调，全部为 BaseTopicCallSingle 子类")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  演示路由
# ---------------------------------------------------------------------------
async def test_router_disabled():
    print("\nTest 6: 未启用 Kafka -> 503")
    app = build_app(publisher=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/kafka/publish", json={"payload": {"x": 1}})
        r2 = await client.get("/kafka/status")
    assert r1.status_code == 503 and r2.status_code == 503
    print("  publish / status 均 503")
    print("  ✅ 通过")


async def test_router_enabled():
    print("\nTest 7: 启用 Kafka -> 发布 200 + 状态 200")
    raw = _FakeRawProducer()
    publisher = EventPublisher(_FakeProducerClient(raw))
    app = build_app(publisher)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/kafka/publish", json={"payload": {"hello": "kafka"}, "key": "k1"})
        assert r.status_code == 200
        body = r.json()
        assert body["message"] == "published"
        assert body["topic"] == DEFAULT_TOPIC
        assert raw.sent[0][0] == DEFAULT_TOPIC

        s = await client.get("/kafka/status")
        assert s.status_code == 200
        status_body = s.json()
        assert status_body["enabled"] is True
        assert status_body["publish"]["published"] == 1
        assert "echo_demo_handled" in status_body["consumed"]

        # 指定自定义 topic
        r2 = await client.post("/kafka/publish", json={"payload": "text", "topic": "CUSTOM"})
        assert r2.json()["topic"] == "CUSTOM"
    print(f"  默认 topic={DEFAULT_TOPIC}，自定义 topic 亦生效")
    print("  ✅ 通过")


def test_config_default():
    print("\nTest 8: MQ 配置默认关闭")
    assert MQ_CONFIG["enabled"] is False
    assert MQ_CONFIG["bootstrap_servers"]
    print("  enabled=False（[myproject.mq] enabled / FS_KAFKA_ENABLED 控制）")
    print("  ✅ 通过")


async def main():
    print("🚀 Kafka 链路模块测试开始")
    try:
        await test_publisher_success()
        await test_publisher_not_connected()
        await test_publisher_send_error()
        await test_echo_callback()
        test_get_consumer_callbacks()
        await test_router_disabled()
        await test_router_enabled()
        test_config_default()
        print("\n" + "=" * 60)
        print("🎉 所有 Kafka 链路模块测试通过！（8 项）")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
