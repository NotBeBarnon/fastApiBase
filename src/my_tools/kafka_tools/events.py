# @Description : Kafka 事件发布封装（零 aiokafka 依赖，可独立测试）
from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger

__all__ = ("EventPublisher",)


class EventPublisher:
    """
    包装 KafkaProducerClient 的便捷发布器：
    - payload 自动 JSON 序列化（bytes 编码）
    - key 可选（同 key 路由到同分区，保证顺序）
    - 发布成功 / 失败计数（/kafka/status 可查）
    - producer 未连接时抛 RuntimeError（fail-fast，不静默丢消息）
    """

    def __init__(self, producer: Any) -> None:
        self._producer = producer
        self.published: int = 0
        self.failed: int = 0
        self.started_at: float = time.time()

    async def publish(self, topic: str, payload: dict | str | bytes, *, key: str | None = None) -> dict:
        """发布消息到指定 topic，返回发送元信息。"""
        with self._producer as raw:
            if raw is None:
                self.failed += 1
                raise RuntimeError(f"Kafka producer not connected, topic={topic}")

            value = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            encoded_key = key.encode("utf-8") if key else None
            try:
                meta = await raw.send_and_wait(topic, value=value, key=encoded_key)
            except Exception:
                self.failed += 1
                raise
            self.published += 1
            result = {
                "topic": topic,
                "partition": getattr(meta, "partition", None),
                "offset": getattr(meta, "offset", None),
            }
            logger.bind(topic=topic, key=key).info(f"kafka published: {topic} partition={result['partition']}")
            return result

    def stats(self) -> dict:
        return {
            "published": self.published,
            "failed": self.failed,
            "uptime_seconds": round(time.time() - self.started_at, 1),
        }
