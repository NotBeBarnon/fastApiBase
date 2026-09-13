# @Description : FastAPI 生命周期（lifespan）
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from loguru import logger
from tortoise import Tortoise

from ..my_tools.redis_tools.clients import RedisClient, RedisSentinelClient
from ..settings import AUTO_SCHEMA, DATABASE_CONFIG, MQ_CONFIG, REDIS_CONFIG

__all__ = ("lifespan",)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动与关闭生命周期，所有资源挂在 app.state 上"""

    logger.info("Startup: initializing resources")

    # 1. Tortoise ORM（未配置任何 app 时跳过；连接失败降级运行，/readyz 会暴露 down）
    if DATABASE_CONFIG["apps"]:
        try:
            await Tortoise.init(config=DATABASE_CONFIG)
            if AUTO_SCHEMA:
                # 自动同步表结构（safe 模式不破坏已有表；默认跟随 DEV，容器可设 FS_AUTO_SCHEMA=true）
                await Tortoise.generate_schemas(safe=True)
            logger.info(f"Tortoise-ORM started: {list(Tortoise.apps)}")
        except Exception as exc:
            logger.warning(f"Tortoise-ORM init failed, running without DB: {exc.__class__.__name__}: {exc}")
    else:
        logger.warning("Tortoise-ORM skipped: no apps configured in DATABASE_CONFIG")

    # 1.5 注册 MCP 示例工具
    from ..my_tools.mcp_tools.examples import register_example_tools

    register_example_tools()
    logger.info("MCP example tools registered")

    # 2. Redis 客户端（配置了哨兵则用哨兵模式，否则单连接模式）
    if REDIS_CONFIG["sentinels"]["service"]:
        redis_client: RedisClient | RedisSentinelClient = RedisSentinelClient(
            sentinels=REDIS_CONFIG["sentinels"]["service"],
            service_name=REDIS_CONFIG["sentinels"]["service_name"],
            db=REDIS_CONFIG["db"],
            user=REDIS_CONFIG["user"],
            password=REDIS_CONFIG["password"],
            retry_interval=REDIS_CONFIG["retry_interval"],
        )
    else:
        redis_client = RedisClient(
            REDIS_CONFIG["host"],
            REDIS_CONFIG["port"],
            REDIS_CONFIG["db"],
            user=REDIS_CONFIG["user"],
            password=REDIS_CONFIG["password"],
            retry_interval=REDIS_CONFIG["retry_interval"],
            **REDIS_CONFIG["more_config"],
        )
    redis_client.start()
    with contextlib.suppress(asyncio.TimeoutError):
        async with asyncio.timeout(3):
            await redis_client.wait_connect()
    app.state.redis = redis_client

    # 3. 后台任务与调度（TaskManager 封装 AsyncIOScheduler：注册中心 + 状态跟踪 + SSE 进度）
    from ..my_tools.schedule_tasks import TaskManager
    from ..my_tools.schedule_tasks.examples import register_example_tasks

    task_manager = TaskManager()
    register_example_tasks(task_manager)
    task_manager.start()
    app.state.task_manager = task_manager
    logger.debug(f"TaskManager started: {[t['name'] for t in task_manager.list_tasks()]}")

    # 3.5 Kafka（默认关闭，[myproject.mq] enabled 或 FS_KAFKA_ENABLED 开启；
    #     延迟导入避免未安装 aiokafka 的环境加载失败）
    kafka_producer = None
    kafka_consumer = None
    if MQ_CONFIG["enabled"]:
        from ..my_tools.kafka_tools import EventPublisher
        from ..my_tools.kafka_tools.clients import KafkaConsumerClient, KafkaProducerClient
        from ..my_tools.kafka_tools.examples import get_consumer_callbacks

        kafka_producer = KafkaProducerClient(
            MQ_CONFIG["bootstrap_servers"],
            user=MQ_CONFIG["user"] or None,
            password=MQ_CONFIG["password"] or None,
            retry_interval=MQ_CONFIG["retry_interval"],
        )
        kafka_producer.start()
        with contextlib.suppress(asyncio.TimeoutError):
            async with asyncio.timeout(3):
                await kafka_producer.wait_connect()

        kafka_consumer = KafkaConsumerClient(
            MQ_CONFIG["bootstrap_servers"],
            user=MQ_CONFIG["user"] or None,
            password=MQ_CONFIG["password"] or None,
            group="fastapi-ai-starter",
            retry_interval=MQ_CONFIG["retry_interval"],
        )
        kafka_consumer.register_callbacks(get_consumer_callbacks())
        kafka_consumer.start()

        app.state.kafka_publisher = EventPublisher(kafka_producer)
        logger.info(f"Kafka started: {MQ_CONFIG['bootstrap_servers']}")
    else:
        app.state.kafka_publisher = None
    # 挂到 state 供 /readyz 探针与业务路由取用
    app.state.kafka = kafka_producer
    app.state.kafka_consumer = kafka_consumer

    # 4. LLM 网关
    from ..my_tools.llm_tools import LLMGateway
    from ..settings import LLM_CONFIG

    llm_gateway = LLMGateway(LLM_CONFIG)
    app.state.llm = llm_gateway
    logger.info(f"LLM Gateway started: {list(LLM_CONFIG.providers.keys()) or '(no providers)'}")

    try:
        yield
    finally:
        logger.info("Shutdown: releasing resources")
        task_manager.close()
        if kafka_producer is not None:
            kafka_producer.stop()
        if kafka_consumer is not None:
            kafka_consumer.stop()
        redis_client.stop()
        await Tortoise.close_connections()
        logger.info("Tortoise-ORM shutdown")
