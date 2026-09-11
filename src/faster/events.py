# @Description : FastAPI 生命周期（lifespan）
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from loguru import logger
from tortoise import Tortoise

from ..my_tools.redis_tools.clients import RedisClient, RedisSentinelClient
from ..my_tools.schedule_tasks.scheduleUtils import quarterly_task
from ..settings import DATABASE_CONFIG, DEFAULT_TIMEZONE, MQ_CONFIG, REDIS_CONFIG

__all__ = ("lifespan",)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动与关闭生命周期，所有资源挂在 app.state 上"""

    logger.info("Startup: initializing resources")

    # 1. Tortoise ORM（未配置任何 app 时跳过初始化，等模型注册后再启用）
    if DATABASE_CONFIG["apps"]:
        await Tortoise.init(config=DATABASE_CONFIG)
        logger.info(f"Tortoise-ORM started: {Tortoise.apps}")
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

    # 3. 定时任务
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        quarterly_task,
        CronTrigger(month="1,4,7,10", day=1, hour=0, minute=0, second=0, timezone=DEFAULT_TIMEZONE),
    )
    scheduler.start()
    app.state.scheduler = scheduler
    logger.debug("Scheduler started")

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
        scheduler.shutdown(wait=False)
        if kafka_producer is not None:
            kafka_producer.stop()
        if kafka_consumer is not None:
            kafka_consumer.stop()
        redis_client.stop()
        await Tortoise.close_connections()
        logger.info("Tortoise-ORM shutdown")
