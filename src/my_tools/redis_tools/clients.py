# -*- coding: utf-8 -*-
# @Description : Redis 客户端（redis>=5.0，asyncio + 哨兵）
from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any

import redis.asyncio as aioredis
from fastapi.responses import Response
from loguru import logger
from redis.asyncio.sentinel import Sentinel
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from src.my_tools.singleton_tools import SingletonABCMeta
from src.settings import REDIS_CONFIG

__all__ = (
    "CLEAN_LUA_SCRIPT",
    "RedisNamespace",
    "RedisNamespaceABC",
    "RedisClient",
    "RedisSentinelClient",
    "SentinelNodeEnum",
)

CLEAN_LUA_SCRIPT = """
local cursor = "0"
repeat
    local result = redis.call("SCAN", cursor, "MATCH", ARGV[1], "COUNT", 10)
    cursor = result[1]
    for _, key in ipairs(result[2]) do
        redis.call("del", key)
    end
until cursor == "0"
"""


# ---------------------------------------------------------------------------
# Namespace
# ---------------------------------------------------------------------------
class RedisNamespaceABC(metaclass=SingletonABCMeta):
    namespace: bytes

    @classmethod
    def get_namespace(cls) -> str:
        return cls.namespace.decode("utf-8")

    @classmethod
    def get_namespace_bytes(cls) -> bytes:
        return cls.namespace

    @classmethod
    def key(cls, key: str | bytes) -> bytes:
        if isinstance(key, str):
            key = key.encode("utf-8")
        return cls.namespace + b":" + key

    @classmethod
    def hash_http_cache_key(cls, route: str, query: str | None = None) -> bytes:
        hash_query = hashlib.md5(query.encode("utf-8")).hexdigest().encode("utf-8") if query else b""
        return cls.namespace + b":http_cache:" + route.encode("utf-8") + b":" + hash_query

    @classmethod
    def get_all_keys_by_route(cls, route: str) -> str:
        return cls.namespace.decode("utf-8") + ":" + route + "*"


class RedisNamespace(RedisNamespaceABC):
    namespace = REDIS_CONFIG["namespace"].encode("utf-8")


# ---------------------------------------------------------------------------
# 公共基类：单连接 / 哨兵 共享重连状态机
# ---------------------------------------------------------------------------
class _BaseRedisClient:
    """共享生命周期 + 自动重连模板"""

    retry_interval: int
    conn_alive_flag: bool = False

    def __init__(self) -> None:
        self._conn_alive_event: asyncio.Event | None = None
        self._conn_success_event: asyncio.Event | None = None
        self._conn_stop_event: asyncio.Event | None = None

    # —— 子类实现 ——
    async def _connect(self) -> bool: ...
    async def _release(self) -> None: ...
    def _label(self) -> str: ...

    async def _init_loop(self) -> None:
        while self.conn_alive_flag:
            logger.info(f"Connect {self._label()}")
            try:
                ok = await self._connect()
                if ok:
                    self._conn_success_event.set()
                    logger.success(f"Connected {self._label()}")
                    return
            except Exception as exc:
                logger.warning(f"Failed to connect {self._label()}: {exc.__class__.__name__}:{exc}")
            await asyncio.sleep(self.retry_interval)

    async def _start(self) -> None:
        if not self._conn_stop_event.is_set():
            logger.warning(f"{self._label()} already running")
            return
        self._conn_stop_event.clear()
        logger.success(f"{self._label()} begin start ...")

        while self.conn_alive_flag:
            self._conn_alive_event.clear()
            self._conn_success_event.clear()
            await self._init_loop()

            await self._conn_alive_event.wait()
            logger.warning(f"{self._label()} disconnected ... reconnect={self.conn_alive_flag}")
            await self._release()

        self._conn_stop_event.set()
        self._conn_success_event.set()
        self._conn_alive_event.set()
        logger.warning(f"{self._label()} client stopped")

    def _stop_signal(self) -> None:
        if isinstance(self._conn_success_event, asyncio.Event):
            self._conn_success_event.clear()
        if isinstance(self._conn_alive_event, asyncio.Event):
            self._conn_alive_event.set()

    async def wait_connect(self) -> None:
        if isinstance(self._conn_success_event, asyncio.Event):
            await self._conn_success_event.wait()

    async def wait_stop(self) -> None:
        if isinstance(self._conn_stop_event, asyncio.Event):
            await self._conn_stop_event.wait()

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


# ---------------------------------------------------------------------------
# 单连接客户端
# ---------------------------------------------------------------------------
class RedisClient(_BaseRedisClient):
    """普通 Redis 单连接客户端"""

    def __init__(
        self,
        host: str,
        port: int,
        db: int,
        *,
        user: str | None = None,
        password: str | None = None,
        retry_interval: int = 10,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.db = db
        self.user = user or None
        self.password = password or None
        self.retry_interval = retry_interval
        self._pass_param = kwargs
        self._pool_client: aioredis.Redis | None = None

    def _label(self) -> str:
        return f"Redis<{self.user}:{self.password}@{self.host}:{self.port}>"

    def set_host(self, host: str, port: int) -> tuple[str, int]:
        if (self.host, self.port) != (host, port):
            self.host = host
            self.port = port
            self.restart()
        return self.host, self.port

    async def _connect(self) -> bool:
        client = aioredis.Redis(
            host=self.host,
            port=self.port,
            db=self.db,
            username=self.user,
            password=self.password,
            **self._pass_param,
        )
        await client.set(RedisNamespace.get_namespace_bytes(), b"alive", ex=self.retry_interval * 2)
        self._pool_client = client
        return True

    async def _release(self) -> None:
        if self._pool_client is not None:
            client = self._pool_client
            self._pool_client = None
            await client.aclose()

    def get_client(self, node: Any = None) -> RedisClient:
        return self

    def client(self, node: Any = None) -> aioredis.Redis | None:
        return self._pool_client

    def __enter__(self) -> aioredis.Redis | None:
        return self._pool_client

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return _handle_redis_exc(exc_type, exc_val, self, on_conn_err=self.restart)

    async def __aenter__(self) -> aioredis.Redis | None:
        return self._pool_client

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        return _handle_redis_exc(exc_type, exc_val, self, on_conn_err=self.restart)

    def __call__(self, key: bytes | str, response: Response, ex_pttl: int = 3000) -> Callable:
        """FastAPI 缓存装饰器"""

        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                if self._pool_client is not None:
                    with contextlib.suppress(Exception):
                        value = await self._pool_client.get(key)
                        if value is not None:
                            await self._pool_client.pexpire(key, ex_pttl)
                            response.body = value
                            return response
                return await func(*args, **kwargs)

            return wrapper

        return decorator

    async def clean_cache_for_route(self, route: str) -> bool:
        if self._pool_client is None:
            return False
        try:
            await self._pool_client.eval(CLEAN_LUA_SCRIPT, 0, RedisNamespace.get_all_keys_by_route(route))
        except Exception as exc:
            logger.error(exc)
            return False
        return True


# ---------------------------------------------------------------------------
# 哨兵客户端
# ---------------------------------------------------------------------------
class SentinelNodeEnum(str, Enum):
    master = "MASTER"
    slave = "SLAVE"


@dataclass
class RedisSentinelClient(_BaseRedisClient):
    sentinels: set[tuple[str, int]] = field(default_factory=set)
    service_name: str = "mymaster"
    db: int = 0
    user: str | None = None
    password: str | None = None
    retry_interval: int = 10

    def __init__(
        self,
        sentinels: Iterable[tuple[str, int]],
        *,
        service_name: str = "mymaster",
        db: int = 0,
        user: str | None = None,
        password: str | None = None,
        retry_interval: int = 10,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.sentinels = set(sentinels)
        self.service_name = service_name
        self.db = db
        self.user = user or None
        self.password = password or None
        self.retry_interval = retry_interval
        self._pass_param = kwargs

        self._sentinel_manager: Sentinel | None = None
        self._node: SentinelNodeEnum | None = None
        self._master_redis: aioredis.Redis | None = None
        self._slave_redis: aioredis.Redis | None = None

    def _label(self) -> str:
        return f"RedisSentinel<{self.user}:{self.password}@{self.sentinels}>"

    async def _connect(self) -> bool:
        sentinel = Sentinel(
            sentinels=list(self.sentinels),
            db=self.db,
            password=self.password,
            username=self.user,
            **self._pass_param,
        )
        master = sentinel.master_for(self.service_name)
        await master.set(RedisNamespace.get_namespace_bytes(), b"alive", ex=self.retry_interval * 2)
        self._sentinel_manager = sentinel
        self._master_redis = master
        self._slave_redis = sentinel.slave_for(self.service_name)
        return True

    async def _release(self) -> None:
        if self._sentinel_manager is None:
            return
        sentinel = self._sentinel_manager
        self._sentinel_manager = None
        self._master_redis = None
        self._slave_redis = None
        await asyncio.gather(
            *(s.aclose() for s in sentinel.sentinels),
            return_exceptions=True,
        )

    def get_client(self, node: SentinelNodeEnum) -> RedisSentinelClient:
        self._node = node
        return self

    def client(self, node: SentinelNodeEnum) -> aioredis.Redis | None:
        if node == SentinelNodeEnum.master:
            return self._master_redis
        if node == SentinelNodeEnum.slave:
            return self._slave_redis
        return None

    def _current(self) -> aioredis.Redis | None:
        if self._sentinel_manager is None:
            return None
        if self._node == SentinelNodeEnum.master:
            return self._master_redis
        if self._node == SentinelNodeEnum.slave:
            return self._slave_redis
        return None

    def __enter__(self) -> aioredis.Redis | None:
        return self._current()

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return _handle_redis_exc(exc_type, exc_val, self)

    async def __aenter__(self) -> aioredis.Redis | None:
        return self._current()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        return _handle_redis_exc(exc_type, exc_val, self)

    def __call__(self, key: bytes | str, response: Response, ex_pttl: int = 3000) -> Callable:
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                if self._sentinel_manager is not None:
                    with contextlib.suppress(Exception):
                        slave = self._sentinel_manager.slave_for(self.service_name)
                        value = await slave.get(key)
                        if value is not None:
                            await self._sentinel_manager.master_for(self.service_name).pexpire(key, ex_pttl)
                            response.body = value
                            return response
                return await func(*args, **kwargs)

            return wrapper

        return decorator

    async def clean_cache_for_route(self, route: str) -> bool:
        if self._sentinel_manager is None:
            return False
        try:
            await self._sentinel_manager.master_for(self.service_name).eval(
                CLEAN_LUA_SCRIPT, 0, RedisNamespace.get_all_keys_by_route(route)
            )
        except Exception as exc:
            logger.error(exc)
            return False
        return True


# ---------------------------------------------------------------------------
# 异常处理工具
# ---------------------------------------------------------------------------
def _handle_redis_exc(exc_type, exc_val, owner: _BaseRedisClient, on_conn_err: Callable | None = None) -> bool:
    if exc_type is None:
        return False
    if isinstance(exc_val, (RedisConnectionError, RedisTimeoutError)):
        logger.error(f"Redis Error {exc_type}:{exc_val}")
        if on_conn_err is not None:
            on_conn_err()
        return True
    if isinstance(exc_val, AssertionError):
        logger.warning(f"{owner._label()} -- {exc_type}:{exc_val}")
        return True
    return False
