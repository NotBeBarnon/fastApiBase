# @Description : Kafka 消费回调示例（演示用，按需替换为业务逻辑）
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from .callbacks import BaseTopicCallSingle

if TYPE_CHECKING:
    from aiokafka import ConsumerRecord

__all__ = ("EchoTopicCall", "get_consumer_callbacks",)


class EchoTopicCall(BaseTopicCallSingle):
    """示例回调：消费消息并记录日志 + 维护处理计数（供 /kafka/status 查询）"""

    handled: int = 0

    @property
    def topic(self) -> str:
        from src.settings import MQ_CONFIG

        return str(MQ_CONFIG["topics"].get("consumer", {}).get("fast_sample", "FAST_SAMPLE"))

    async def callback(self, msg: ConsumerRecord) -> None:
        EchoTopicCall.handled += 1
        try:
            payload = json.loads(msg.value)
        except (ValueError, TypeError):
            payload = msg.value
        logger.bind(topic=msg.topic, offset=msg.offset).info(f"kafka consumed: {payload}")


def get_consumer_callbacks() -> list[type[BaseTopicCallSingle]]:
    """返回要注册的消费者回调列表（新增业务回调在此追加）"""
    return [EchoTopicCall]
