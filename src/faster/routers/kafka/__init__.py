# @Description : Kafka 演示路由（消息发布 / 链路状态）
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.settings import MQ_CONFIG

__all__ = ("kafka_router",)

kafka_router = APIRouter(prefix="/kafka", tags=["kafka"])

DEFAULT_TOPIC = str(MQ_CONFIG["topics"].get("producer", {}).get("fast_sample", "FAST_SAMPLE"))


class PublishRequest(BaseModel):
    payload: dict | str = Field(..., description="消息体（dict 自动 JSON 序列化）")
    topic: str | None = Field(None, description="目标主题，默认取配置 topics.producer.fast_sample")
    key: str | None = Field(None, description="分区路由键（同 key 保证顺序）")


def _require_publisher(request: Request):
    publisher = getattr(request.app.state, "kafka_publisher", None)
    if publisher is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Kafka disabled: set [myproject.mq] enabled = true or FS_KAFKA_ENABLED=true",
        )
    return publisher


@kafka_router.post("/publish", summary="发布一条消息到 Kafka")
async def publish_message(body: PublishRequest, request: Request) -> dict:
    publisher = _require_publisher(request)
    result = await publisher.publish(body.topic or DEFAULT_TOPIC, body.payload, key=body.key)
    return {"message": "published", **result}


@kafka_router.get("/status", summary="Kafka 链路状态（生产/消费计数）")
async def kafka_status(request: Request) -> dict:
    publisher = _require_publisher(request)
    from src.my_tools.kafka_tools.examples import EchoTopicCall

    return {
        "enabled": True,
        "bootstrap_servers": list(MQ_CONFIG["bootstrap_servers"]),
        "default_topic": DEFAULT_TOPIC,
        "publish": publisher.stats(),
        "consumed": {"echo_demo_handled": EchoTopicCall.handled},
    }
