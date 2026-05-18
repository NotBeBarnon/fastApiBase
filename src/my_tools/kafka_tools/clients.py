# -*- coding: utf-8 -*-
# @Description : Kafka 客户端（aiokafka，自动重连 + 自动建主题）
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import aiokafka
from aiokafka import AIOKafkaClient, AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord
from aiokafka.conn import AIOKafkaConnection
from kafka.admin import NewTopic
from kafka.errors import KafkaConnectionError, KafkaTimeoutError
from kafka.protocol.admin import (
    CreateTopicsRequest_v1,
    CreateTopicsResponse_v1,
    DeleteTopicsRequest_v1,
)
from kafka.protocol.metadata import MetadataRequest_v1, MetadataResponse_v1
from loguru import logger

from src.settings import MQ_CONFIG

from .callbacks import BaseTopicCall


class TopicConfig(NewTopic):
    name: str
    num_partitions: int
    replication_factor: int
    topic_configs: dict | None = None

    def __init__(
        self,
        name: str,
        num_partitions: int,
        replication_factor: int = 0,
        replica_assignments: dict | None = None,
        topic_configs: dict | None = None,
    ) -> None:
        super().__init__(name, num_partitions, replication_factor, replica_assignments, topic_configs)


class CreateTopicMixin:
    num_partitions: int = MQ_CONFIG["num_partitions"]
    replication_factor: int = MQ_CONFIG["replication_factor"]
    topic_configs: dict = MQ_CONFIG["topic_configs"]

    create_topic_timeout: int = 3

    def __init__(self) -> None:
        self.topics_conf: dict[str, NewTopic] = {}
        self.controller_node_id: int | None = None
        self.created_topics: set[str] = set()
        self.exist_topics: set[str] = set()

    def set_topics_conf(self, conf: dict[str, NewTopic]) -> None:
        self.topics_conf.update(conf)

    async def _get_controller_node_id(self, client: AIOKafkaClient) -> int | None:
        for node_id in (b.nodeId for b in client.cluster.brokers()):
            conn: AIOKafkaConnection = await client._get_conn(node_id)
            fut: MetadataResponse_v1 = await conn.send(MetadataRequest_v1())
            self.controller_node_id = fut.controller_id
            break
        return self.controller_node_id

    async def _get_topics(self, client: AIOKafkaClient) -> set[str]:
        topics: set[str] = set()
        for node_id in (b.nodeId for b in client.cluster.brokers()):
            conn: AIOKafkaConnection = await client._get_conn(node_id)
            fut: MetadataResponse_v1 = await conn.send(MetadataRequest_v1())
            topics = {name for _, name, _, _ in fut.topics}
            break
        self.exist_topics |= topics
        return topics

    async def _delete_topics(
        self, client: AIOKafkaClient, topics_name_set: Iterable[str]
    ) -> tuple[set[str], dict[str, int]]:
        req = DeleteTopicsRequest_v1(topics=list(topics_name_set), timeout=3000)
        node_id = await self._get_controller_node_id(client)
        conn: AIOKafkaConnection = await client._get_conn(node_id)
        fut: CreateTopicsResponse_v1 = await conn.send(req)

        success: set[str] = set()
        failed: dict[str, int] = {}
        for item in fut.to_object()["topic_error_codes"]:
            if item["error_code"]:
                failed[item["topic"]] = item["error_code"]
            else:
                success.add(item["topic"])
        self.exist_topics -= success
        self.created_topics -= success
        return success, failed

    async def _create_topics(
        self, client: AIOKafkaClient, conf: list[NewTopic]
    ) -> tuple[set[str], dict[str, tuple[int, str]]]:
        req = CreateTopicsRequest_v1(
            create_topic_requests=[self.convert_new_topic_request(t) for t in conf],
            timeout=3000,
        )
        node_id = await self._get_controller_node_id(client)
        conn: AIOKafkaConnection = await client._get_conn(node_id)
        fut: CreateTopicsResponse_v1 = await conn.send(req)

        success: set[str] = set()
        failed: dict[str, tuple[int, str]] = {}
        for item in fut.to_object()["topic_errors"]:
            if item["error_code"]:
                failed[item["topic"]] = (item["error_code"], item["error_message"])
            else:
                success.add(item["topic"])

        already_exists = {t for t, (code, _) in failed.items() if code == 36}
        success |= already_exists
        self.exist_topics |= success
        self.created_topics |= success
        return success, failed

    async def _auto_create_topics(
        self,
        client: AIOKafkaClient,
        conf: dict[str, NewTopic],
        *,
        start_client: bool = False,
    ) -> None:
        self.topics_conf.update(conf)
        logger.info(f"Create Topics: {list(conf.keys())}")

        req = CreateTopicsRequest_v1(
            create_topic_requests=[self.convert_new_topic_request(t) for t in conf.values()],
            timeout=self.create_topic_timeout * 1000,
        )

        async with asyncio.timeout(self.create_topic_timeout):
            if start_client:
                await client.bootstrap()
            node_id = await self._get_controller_node_id(client)
            conn: AIOKafkaConnection = await client._get_conn(node_id)
            fut: CreateTopicsResponse_v1 = await conn.send(req)

        if start_client:
            await client.close()

        success: set[str] = set()
        failed: dict[str, tuple[int, str]] = {}
        for item in fut.to_object()["topic_errors"]:
            if item["error_code"]:
                failed[item["topic"]] = (item["error_code"], item["error_message"])
            else:
                success.add(item["topic"])

        already_exists = {t for t, (code, _) in failed.items() if code == 36}
        success |= already_exists
        self.exist_topics |= success
        self.created_topics |= success

        success and logger.success(f"Create Topics Success: {success}")
        failed and logger.warning(f"Create Topics Failed: {failed}")

    @classmethod
    def convert_new_topic_request(cls, new_topic: NewTopic) -> tuple:
        return (
            new_topic.name,
            new_topic.num_partitions,
            new_topic.replication_factor,
            list(new_topic.replica_assignments.items()),
            list(new_topic.topic_configs.items()),
        )


class _BaseKafkaClient(CreateTopicMixin):
    """Kafka 客户端公共生命周期"""

    user: str | None
    password: str | None
    bootstrap_servers: set[str]
    retry_interval: int
    conn_alive_flag: bool = False

    def __init__(self) -> None:
        super().__init__()
        self._conn_alive_event: asyncio.Event | None = None
        self._conn_success_event: asyncio.Event | None = None
        self._conn_stop_event: asyncio.Event | None = None

    # —— 子类实现 ——
    async def _init_client(self) -> None: ...
    async def _release_client(self) -> None: ...
    def _label(self) -> str: ...

    async def _start(self) -> None:
        if not self._conn_stop_event.is_set():
            logger.warning(f"{self._label()} is already running")
            return
        logger.success(f"{self._label()} begin start ...")
        self._conn_stop_event.clear()

        while self.conn_alive_flag:
            self._conn_alive_event.clear()
            self._conn_success_event.clear()
            await self._init_client()
            await self._after_connect()

            await self._conn_alive_event.wait()
            logger.warning(f"{self._label()} disconnected ... reconnect={self.conn_alive_flag}")
            await self._release_client()

        self._conn_stop_event.set()
        self._conn_success_event.set()
        self._conn_alive_event.set()
        logger.warning(f"{self._label()} client stopped")

    async def _after_connect(self) -> None:
        """连接成功后的钩子（消费者会创建监听 task）"""

    def _stop_signal(self) -> None:
        if isinstance(self._conn_success_event, asyncio.Event):
            self._conn_success_event.clear()
        if isinstance(self._conn_alive_event, asyncio.Event):
            self._conn_alive_event.set()

    def start(self) -> None:
        self.conn_alive_flag = True
        if not isinstance(self._conn_stop_event, asyncio.Event):
            self._conn_alive_event = asyncio.Event()
            self._conn_success_event = asyncio.Event()
            self._conn_stop_event = asyncio.Event()
        self._conn_stop_event.set()
        self._conn_alive_event.clear()
        self._conn_success_event.clear()
        asyncio.create_task(self._start())

    def stop(self) -> None:
        self.conn_alive_flag = False
        logger.warning(f"Call Stop {self._label()}")
        self._stop_signal()

    def restart(self) -> None:
        logger.warning(f"Call Restart {self._label()}")
        self._stop_signal()

    async def wait_connect(self) -> None:
        if isinstance(self._conn_success_event, asyncio.Event):
            await self._conn_success_event.wait()

    async def wait_stop(self) -> None:
        if isinstance(self._conn_stop_event, asyncio.Event):
            await self._conn_stop_event.wait()


class KafkaConsumerClient(_BaseKafkaClient):
    """Kafka 消费者客户端"""

    is_create_topics: bool = False

    def __init__(
        self,
        bootstrap_servers: Iterable[str],
        *,
        user: str | None = None,
        password: str | None = None,
        group: str | None = None,
        retry_interval: int = 10,
        from_now: bool = True,
        async_callbacks: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.bootstrap_servers = set(bootstrap_servers)
        self.user = user or None
        self.password = password or None
        self.group = group or None
        self.retry_interval = retry_interval
        self.from_now = from_now
        self.async_callbacks = async_callbacks
        self._pass_param = kwargs

        self._consumer: AIOKafkaConsumer | None = None
        self._callbacks: dict[str, type[BaseTopicCall]] = {}

    def _label(self) -> str:
        return f"KafkaConsumer<{self.user}:{self.password}@{self.bootstrap_servers}>"

    def update_bootstrap_servers(self, new_bootstrap_servers: Iterable[str]) -> KafkaConsumerClient:
        new_set = set(new_bootstrap_servers)
        if not new_set <= self.bootstrap_servers:
            self.topics_need_create()
        self.bootstrap_servers = new_set
        return self

    def topics_need_create(self) -> KafkaConsumerClient:
        self.is_create_topics = False
        return self

    def register_callbacks(self, calls: list[type[BaseTopicCall]]) -> tuple[set, set]:
        registered: set = set()
        unregistered: set = set()
        logger.debug("-----  Kafka Register Consumer  -------")

        for item in calls:
            if issubclass(item, BaseTopicCall):
                logger.debug(f"Kafka register Consumer <Topic:{item.topic} {item.__name__}>")
                self._callbacks[item.topic] = item
                self.topics_conf[item.topic] = NewTopic(
                    name=item.topic,
                    num_partitions=self.num_partitions,
                    replication_factor=self.replication_factor,
                    topic_configs=self.topic_configs,
                )
                registered.add(item)
            else:
                unregistered.add(item)

        if registered:
            logger.success(f"KafkaConsumer <Registered-{registered}>")
            self.topics_need_create()
        if unregistered:
            logger.error(f"KafkaConsumer <Unregistered-{unregistered}>")
        return registered, unregistered

    async def _init_client(self) -> None:
        self._consumer = None
        while self.conn_alive_flag:
            logger.info(f"Connect {self._label()}")
            try:
                if not self.is_create_topics:
                    await self._auto_create_topics(
                        AIOKafkaClient(
                            bootstrap_servers=self.bootstrap_servers,
                            sasl_plain_username=self.user,
                            sasl_plain_password=self.password,
                        ),
                        self.topics_conf,
                        start_client=True,
                    )
                    self.is_create_topics = True

                consumer = AIOKafkaConsumer(
                    *self.created_topics,
                    bootstrap_servers=self.bootstrap_servers,
                    group_id=self.group,
                    sasl_plain_username=self.user,
                    sasl_plain_password=self.password,
                    **self._pass_param,
                )
                await consumer.start()
                if self.from_now:
                    try:
                        await consumer.seek_to_end()
                    except Exception as exc:
                        logger.warning(f"Kafka Consumer seek_to_end failed - {exc.__class__.__name__}:{exc}")
                self._consumer = consumer
                self._conn_success_event.set()
                logger.success(f"Connected {self._label()}")
                return
            except Exception as exc:
                logger.warning(f"Failed to connect {self._label()} - {exc.__class__.__name__}:{exc}")
                await asyncio.sleep(self.retry_interval)

    async def _after_connect(self) -> None:
        asyncio.create_task(self._listening_consumer())

    async def _listening_consumer(self) -> None:
        try:
            if self.async_callbacks:
                async for msg in self._consumer:
                    asyncio.create_task(self._callback(msg))
            else:
                async for msg in self._consumer:
                    await self._callback(msg)
        finally:
            logger.warning(f"{self._label()} listening stop")
            if self._consumer is not None:
                await self._consumer.stop()
            if isinstance(self._conn_alive_event, asyncio.Event):
                self._conn_alive_event.set()

    async def _callback(self, msg: ConsumerRecord) -> None:
        try:
            await self._callbacks[msg.topic]().callback(msg)
        except Exception as exc:
            logger.exception(f"Kafka callback error {msg.topic}:{msg.value} - {exc.__class__.__name__}:{exc}")

    async def _release_client(self) -> None:
        if self._consumer is not None:
            client = self._consumer
            self._consumer = None
            await client.stop()

    async def delete_topics(self, topics_name_set: Iterable[str]) -> None:
        if self._consumer is None:
            logger.warning(f"Consumer not start, delete topics failed: {topics_name_set}")
            return
        success, failed = await self._delete_topics(self._consumer._client, topics_name_set)
        success and logger.warning(f"Delete Topics Success: {success}")
        failed and logger.warning(f"Delete Topics Failed: {failed}")

    async def get_topics(self) -> set[str]:
        if self._consumer is None:
            logger.warning("Consumer not start, get topics list failed")
            return set()
        return await self._get_topics(self._consumer._client)

    async def create_topics(self, topic_config: list[dict | NewTopic]) -> None:
        if self._consumer is None:
            logger.warning(f"Consumer not start, create topics failed: {topic_config}")
            return
        config = [
            NewTopic(
                name=item.get("name"),
                num_partitions=item.get("num_partitions"),
                replication_factor=item.get("replication_factor"),
                replica_assignments=item.get("replica_assignments"),
                topic_configs=item.get("topic_configs"),
            )
            if isinstance(item, dict)
            else item
            for item in topic_config
            if isinstance(item, NewTopic)
            or (isinstance(item, dict) and item.get("name") and item.get("num_partitions"))
        ]
        success, failed = await self._create_topics(self._consumer._client, config)
        success and logger.success(f"Create Topics Success: {success}")
        failed and logger.warning(f"Create Topics Failed: {failed}")


class KafkaProducerClient(_BaseKafkaClient):
    """Kafka 生产者客户端"""

    def __init__(
        self,
        bootstrap_servers: Iterable[str],
        *,
        user: str | None = None,
        password: str | None = None,
        retry_interval: int = 10,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.bootstrap_servers = set(bootstrap_servers)
        self.user = user or None
        self.password = password or None
        self.retry_interval = retry_interval
        self._pass_param = kwargs
        self._producer: aiokafka.AIOKafkaProducer | None = None

    def _label(self) -> str:
        return f"KafkaProducer<{self.user}:{self.password}@{self.bootstrap_servers}>"

    def update_bootstrap_servers(self, new_bootstrap_servers: Iterable[str]) -> KafkaProducerClient:
        new_set = set(new_bootstrap_servers)
        if not new_set <= self.bootstrap_servers:
            self.restart()
        self.bootstrap_servers = new_set
        return self

    async def _init_client(self) -> None:
        self._producer = None
        while self.conn_alive_flag:
            logger.info(f"Connect {self._label()}")
            try:
                producer = AIOKafkaProducer(
                    bootstrap_servers=self.bootstrap_servers,
                    sasl_plain_username=self.user,
                    sasl_plain_password=self.password,
                    **self._pass_param,
                )
                await producer.start()
                self._producer = producer
                self._conn_success_event.set()
                logger.success(f"Connected {self._label()}")
                return
            except Exception as exc:
                logger.warning(f"Failed to connect {self._label()} - {exc.__class__.__name__}:{exc}")
                await asyncio.sleep(self.retry_interval)

    async def _release_client(self) -> None:
        if self._producer is not None:
            client = self._producer
            self._producer = None
            await client.stop()

    async def get_sender(self, topic: str) -> KafkaProducerClient:
        if topic in self.exist_topics:
            return self
        conf = {
            topic: self.topics_conf.get(
                topic,
                NewTopic(
                    topic,
                    num_partitions=self.num_partitions,
                    replication_factor=self.replication_factor,
                    topic_configs=self.topic_configs,
                ),
            )
        }
        if self._producer is not None:
            try:
                await self._auto_create_topics(self._producer.client, conf)
            except Exception as exc:
                logger.error(f"Create Topic Error - {exc.__class__.__name__}:{exc}")
        return self

    async def delete_topics(self, topics_name_set: Iterable[str]) -> None:
        if self._producer is None:
            logger.warning(f"Producer not start, delete topics failed: {topics_name_set}")
            return
        success, failed = await self._delete_topics(self._producer.client, topics_name_set)
        success and logger.warning(f"Delete Topics Success: {success}")
        failed and logger.warning(f"Delete Topics Failed: {failed}")

    async def get_topics(self) -> set[str]:
        if self._producer is None:
            logger.warning("Producer not start, get topics list failed")
            return set()
        return await self._get_topics(self._producer.client)

    async def create_topics(self, topic_config: list[dict | NewTopic]) -> None:
        if self._producer is None:
            logger.warning(f"Producer not start, create topics failed: {topic_config}")
            return
        config = [
            NewTopic(
                name=item.get("name"),
                num_partitions=item.get("num_partitions"),
                replication_factor=item.get("replication_factor"),
                replica_assignments=item.get("replica_assignments"),
                topic_configs=item.get("topic_configs"),
            )
            if isinstance(item, dict)
            else item
            for item in topic_config
            if isinstance(item, NewTopic)
            or (isinstance(item, dict) and item.get("name") and item.get("num_partitions"))
        ]
        success, failed = await self._create_topics(self._producer.client, config)
        success and logger.success(f"Create Topics Success: {success}")
        failed and logger.warning(f"Create Topics Failed: {failed}")

    def __enter__(self) -> AIOKafkaProducer | None:
        return self._producer

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return _handle_kafka_exc(exc_type, exc_val, self)

    async def __aenter__(self) -> AIOKafkaProducer | None:
        return self._producer

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        return _handle_kafka_exc(exc_type, exc_val, self)


def _handle_kafka_exc(exc_type, exc_val, owner: KafkaProducerClient) -> bool:
    if exc_type is None:
        return False
    if isinstance(exc_val, (KafkaConnectionError, KafkaTimeoutError)):
        logger.error(f"Kafka Error {exc_type}:{exc_val}")
        owner.restart()
        return True
    if isinstance(exc_val, AssertionError):
        logger.warning(f"{owner._label()} -- {exc_type}:{exc_val}")
        return True
    return False
