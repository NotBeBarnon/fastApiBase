# -*- coding: utf-8 -*-
# @Description : 滑动窗口限流（Redis ZSET + Lua 原子操作，Redis 不可用时降级进程内存窗口）
from __future__ import annotations

import math
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status
from loguru import logger

from src.settings import SECURITY_CONFIG

__all__ = ("RateLimitResult", "SlidingWindowLimiter", "get_limiter", "rate_limit")

# Lua 保证「清理过期 + 计数 + 写入 + 续期」原子执行，避免竞态
# KEYS[1]=限流 key  ARGV[1]=窗口毫秒  ARGV[2]=限额  ARGV[3]=当前毫秒  ARGV[4]=唯一成员
# 返回 {allowed(0/1), remaining, retry_after_seconds}
RATE_LIMIT_LUA = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count < limit then
    redis.call('ZADD', key, now, ARGV[4])
    redis.call('PEXPIRE', key, window)
    return {1, limit - count - 1, 0}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry_after = 1
if oldest[2] ~= false then
    retry_after = math.ceil((tonumber(oldest[2]) + window - now) / 1000)
    if retry_after < 1 then retry_after = 1 end
end
return {0, 0, retry_after}
"""


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: float


class SlidingWindowLimiter:
    """
    滑动窗口限流器。

    - 有 Redis 连接时走 Lua 脚本（跨实例精确限流）
    - Redis 不可用 / 异常时降级进程内存窗口（fail-open，仅单实例内生效）
    """

    def __init__(self, key_prefix: str = "rate_limit") -> None:
        self.key_prefix = key_prefix
        self._memory: dict[str, deque[float]] = {}

    async def check(self, redis: Any, key: str, times: int, window_seconds: float) -> RateLimitResult:
        if redis is not None:
            try:
                return await self._check_redis(redis, key, times, window_seconds)
            except Exception as exc:
                logger.warning(f"RateLimit: redis unavailable, fallback to memory: {exc.__class__.__name__}: {exc}")
        return self._check_memory(key, times, window_seconds)

    async def _check_redis(self, redis: Any, key: str, times: int, window_seconds: float) -> RateLimitResult:
        now_ms = int(time.time() * 1000)
        window_ms = int(window_seconds * 1000)
        member = f"{now_ms}-{uuid.uuid4().hex[:8]}"
        raw = await redis.eval(RATE_LIMIT_LUA, 1, f"{self.key_prefix}:{key}", window_ms, times, now_ms, member)
        allowed, remaining, retry_after = int(raw[0]), int(raw[1]), int(raw[2])
        return RateLimitResult(bool(allowed), remaining, float(max(retry_after, 1)))

    def _check_memory(self, key: str, times: int, window_seconds: float) -> RateLimitResult:
        now = time.time()
        window = self._memory.setdefault(key, deque())
        while window and window[0] <= now - window_seconds:
            window.popleft()
        if len(window) < times:
            window.append(now)
            return RateLimitResult(True, times - len(window), 0.0)
        wait = window[0] + window_seconds - now
        return RateLimitResult(False, 0, max(wait, 0.1))

    def reset(self) -> None:
        """清空内存窗口（测试用）"""
        self._memory.clear()


_default_limiter = SlidingWindowLimiter(key_prefix=SECURITY_CONFIG["rate_limit"]["key_prefix"])


def get_limiter() -> SlidingWindowLimiter:
    return _default_limiter


def _raw_redis(client: Any) -> Any:
    """从包装客户端（哨兵/单连接）中提取底层 aioredis 连接，未连接时返回 None"""
    if client is None:
        return None
    return getattr(client, "_master_redis", None) or getattr(client, "_pool_client", None)


def rate_limit(times: int | None = None, window_seconds: int | None = None, scope: str | None = None):
    """
    限流依赖工厂。默认按「客户端 IP + 路径」维度计数，超出窗口限额抛 429 + Retry-After。

    用法：dependencies=[Depends(rate_limit(times=5, window_seconds=10))]
    """

    async def dependency(request: Request) -> RateLimitResult:
        cfg = SECURITY_CONFIG["rate_limit"]
        if not cfg["enabled"]:
            return RateLimitResult(True, -1, 0.0)

        limit = times or cfg["times"]
        window = window_seconds or cfg["window_seconds"]
        limiter = getattr(request.app.state, "rate_limiter", None) or get_limiter()
        redis = _raw_redis(getattr(request.app.state, "redis", None))

        client_ip = request.client.host if request.client else "unknown"
        key = scope or f"ip:{client_ip}:{request.url.path}"
        result = await limiter.check(redis, key, limit, window)

        if not result.allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: {limit} requests per {window}s",
                headers={"Retry-After": str(math.ceil(result.retry_after_seconds))},
            )
        return result

    return dependency
