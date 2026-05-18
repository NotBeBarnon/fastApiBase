# -*- coding: utf-8 -*-
# @Time    : 2022/3/3:19:03
# @Author  : fzx
# @Description :kafka客户端



# -*- coding: utf-8 -*-

"""
kafka 消息队列 (单机使用）
"""
from __future__ import annotations

import asyncio

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord
from kafka.admin import KafkaAdminClient, NewTopic
from loguru import logger


class MessageHandler:
    def __init__(self) -> None: ...

    @staticmethod
    async def message_handler(msg: ConsumerRecord) -> None:
        print("message from kafak: ", msg.key)
        print(msg.value)


class KafkaClient:
    """kafka 客户端"""

    _instance = None
    producer: AIOKafkaProducer | None = None
    start_flag_producer: bool = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = object.__new__(cls)
        return cls._instance

    def __init__(self, host: str = "124.223.5.168", port: int = 9092) -> None:
        self._host = host
        self._port = port
        self.__consumers: list[AIOKafkaConsumer] = []

    def create_topics(self, topics: list, num_partitions=1, replication_factor=3, min_replicas=2):
        """创建队列主题"""
        cluster = KafkaAdminClient(bootstrap_servers=f"{self._host}:{self._port}")
        for topic in topics:
            try:
                new_topic = NewTopic(
                    name=topic,
                    num_partitions=num_partitions,
                    replication_factor=replication_factor,
                    topic_configs={"min.insync.replicas": min_replicas}
                )
                cluster.create_topics(new_topics=[new_topic], validate_only=False)
                logger.success(f"Successfully created topic: {topic}")

            except (ValueError, Exception):
                logger.warning(f"The topic: {topic} already exists ")

    async def close_connection(self):
        """关闭Kafak连接"""
        if self.start_flag_producer:
            await self.producer.stop()

        for consumer in self.__consumers:
            await consumer.stop()
            logger.success(f"{consumer} closed!")

    async def get_producer(self) -> AIOKafkaProducer:
        """创建一个消息生产者"""
        if not self.start_flag_producer:
            self.producer = AIOKafkaProducer(bootstrap_servers=f"{self._host}:{self._port}", acks="all")
            await self.producer.start()
            self.start_flag_producer = True

        return self.producer

    async def consumer_worker(self, topic: str):
        """消费者任务，处理从Kafka中收到的数据"""
        message_handler = MessageHandler()
        consumer = AIOKafkaConsumer(topic, bootstrap_servers=f'{self._host}:{self._port}')
        await consumer.start()
        # 记录消费者
        self.__consumers.append(consumer)
        logger.success('Kafka consumer started!')

        async for msg in consumer:
            # logger.info(
            #     "{}:{:d}:{:d}: key={} value={} timestamp_ms={}".format(
            #         msg.topic, msg.partition, msg.offset, msg.key, msg.value, msg.timestamp
            #     )
            # )
            await message_handler.message_handler(msg)



async def _demo() -> None:
    kfk = KafkaClient(host="124.223.5.168")
    kfk.create_topics(topics=["test_topic", "test_fzx"])
    producer = await kfk.get_producer()
    await producer.send(topic="test_topic", value=b"123456")


if __name__ == "__main__":
    asyncio.run(_demo())