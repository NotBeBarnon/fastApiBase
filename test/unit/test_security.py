# -*- coding: utf-8 -*-
# @Description : 里程碑 5 单元测试（JWT / API Key / 限流 / 请求 ID 中间件）
from __future__ import annotations

import asyncio
import sys
import time

import httpx
from fastapi import FastAPI

from src.faster.middlewares import RequestContextMiddleware
from src.faster.routers.security import security_router
from src.my_tools.security_tools import (
    SlidingWindowLimiter,
    TokenError,
    create_token,
    decode_token,
    get_limiter,
)
from src.settings import SECURITY_CONFIG

SECRET = SECURITY_CONFIG["jwt_secret"]
ADMIN_KEY = SECURITY_CONFIG["api_keys"]["admin"]["key"]
DEMO_KEY = SECURITY_CONFIG["api_keys"]["demo"]["key"]


def build_app() -> FastAPI:
    """不带 lifespan 的最小 app：跳过 DB/Redis 初始化，专注测安全层"""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    app.include_router(security_router)
    return app


class _FakeRedis:
    """mock 底层 aioredis 连接的 eval"""

    def __init__(self, result=None, exc: Exception | None = None):
        self._result = result
        self._exc = exc
        self.eval_calls: list[tuple] = []

    async def eval(self, script, numkeys, key, *args):
        self.eval_calls.append((key, args))
        if self._exc is not None:
            raise self._exc
        return self._result


# ---------------------------------------------------------------------------
#  JWT 编解码
# ---------------------------------------------------------------------------
def test_jwt_round_trip():
    print("\nTest 1: JWT 签发 / 解码 round-trip")
    token = create_token({"sub": "alice", "role": "admin"}, SECRET, expires_seconds=60)
    assert isinstance(token, str) and token.count(".") == 2
    payload = decode_token(token, SECRET)
    assert payload["sub"] == "alice"
    assert payload["role"] == "admin"
    assert "iat" in payload and "exp" in payload and "jti" in payload
    assert payload["exp"] - payload["iat"] == 60

    hs512 = decode_token(create_token({"x": 1}, SECRET, algorithm="HS512"), SECRET, algorithms=("HS512",))
    assert hs512["x"] == 1
    print(f"  sub={payload['sub']} jti={payload['jti'][:8]}... HS512 兼容")
    print("  ✅ 通过")


def test_jwt_expired():
    print("\nTest 2: JWT 过期")
    token = create_token({"sub": "bob"}, SECRET, expires_seconds=-10)
    try:
        decode_token(token, SECRET)
    except TokenError as exc:
        assert "expired" in str(exc)
    else:
        raise AssertionError("过期 token 未被拒绝")
    print("  ✅ 通过")


def test_jwt_tampered():
    print("\nTest 3: JWT 篾改签名 / 换密钥")
    token = create_token({"sub": "alice"}, SECRET)
    # 改签名末位
    head, body, sig = token.split(".")
    bad_sig = ("A" if sig[-1] != "A" else "B") + sig[1:]
    try:
        decode_token(f"{head}.{body}.{bad_sig}", SECRET)
    except TokenError:
        pass
    else:
        raise AssertionError("篾改签名未被拒绝")
    # 换密钥验签
    try:
        decode_token(token, "wrong-secret")
    except TokenError:
        pass
    else:
        raise AssertionError("错误密钥未被拒绝")
    print("  ✅ 通过")


def test_jwt_invalid():
    print("\nTest 4: JWT 非法格式 / 算法不在白名单")
    for bad in ("not-a-token", "a.b", "a.b.c.d"):
        try:
            decode_token(bad, SECRET)
        except TokenError:
            pass
        else:
            raise AssertionError(f"非法格式未被拒绝: {bad}")
    hs512_token = create_token({"x": 1}, SECRET, algorithm="HS512")
    try:
        decode_token(hs512_token, SECRET, algorithms=("HS256",))
    except TokenError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("白名单外算法未被拒绝")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  滑动窗口限流器（内存 / Redis / 兑底）
# ---------------------------------------------------------------------------
async def test_limiter_memory():
    print("\nTest 5: 内存滑动窗口（限额内通过 / 超限拒绝 / 窗口滑动恢复）")
    limiter = SlidingWindowLimiter()
    results = [await limiter.check(None, "k1", times=3, window_seconds=0.3) for _ in range(3)]
    assert all(r.allowed for r in results)
    assert results[-1].remaining == 0

    blocked = await limiter.check(None, "k1", times=3, window_seconds=0.3)
    assert not blocked.allowed
    assert blocked.retry_after_seconds > 0

    await asyncio.sleep(0.35)  # 窗口滑出
    recovered = await limiter.check(None, "k1", times=3, window_seconds=0.3)
    assert recovered.allowed
    print(f"  超限 retry_after={blocked.retry_after_seconds:.2f}s，滑动后恢复")
    print("  ✅ 通过")


async def test_limiter_redis():
    print("\nTest 6: Redis Lua 限流（allowed / blocked 两条路径）")
    limiter = SlidingWindowLimiter()
    ok_redis = _FakeRedis(result=[1, 4, 0])
    r = await limiter.check(ok_redis, "k2", times=5, window_seconds=10)
    assert r.allowed and r.remaining == 4
    key, args = ok_redis.eval_calls[0]
    assert key == "rate_limit:k2"
    assert args[0] == 10000 and args[1] == 5  # window_ms / times

    no_redis = _FakeRedis(result=[0, 0, 3])
    r2 = await limiter.check(no_redis, "k2", times=5, window_seconds=10)
    assert not r2.allowed and r2.retry_after_seconds == 3
    print("  allowed {1,4,0} / blocked {0,0,3} 解析正确")
    print("  ✅ 通过")


async def test_limiter_redis_fallback():
    print("\nTest 7: Redis 异常 -> 内存兑底（fail-open）")
    limiter = SlidingWindowLimiter()
    broken = _FakeRedis(exc=ConnectionError("redis down"))
    r = await limiter.check(broken, "k3", times=2, window_seconds=60)
    assert r.allowed
    r2 = await limiter.check(broken, "k3", times=2, window_seconds=60)
    assert r2.allowed
    r3 = await limiter.check(broken, "k3", times=2, window_seconds=60)
    assert not r3.allowed
    print("  Redis 不可用时降级内存窗口，仍能限流")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  集成：鉴权 + 限流 + 中间件（ASGITransport，无 lifespan）
# ---------------------------------------------------------------------------
async def test_api_key_flow():
    print("\nTest 8: API Key 鉴权（正确 / 角色不足 / 错误 / 缺失）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        r = await client.get("/security/admin", headers={"X-API-Key": ADMIN_KEY})
        assert r.status_code == 200
        assert r.json()["message"].startswith("Welcome, admin")

        r403 = await client.get("/security/admin", headers={"X-API-Key": DEMO_KEY})
        assert r403.status_code == 403

        r401 = await client.get("/security/admin", headers={"X-API-Key": "wrong"})
        assert r401.status_code == 401

        r_missing = await client.get("/security/admin")
        assert r_missing.status_code == 401
    print("  200 / 403 / 401 / 401 全部正确")
    print("  ✅ 通过")


async def test_jwt_flow():
    print("\nTest 9: JWT 签发 -> /me 回读 / 无 token / 坏 token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        issued = await client.post("/security/token", json={"subject": "alice", "role": "admin", "expires_seconds": 60})
        assert issued.status_code == 200
        token = issued.json()["access_token"]

        r = await client.get("/security/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["subject"] == "alice"
        assert r.json()["role"] == "admin"

        no = await client.get("/security/me")
        assert no.status_code == 401

        bad = await client.get("/security/me", headers={"Authorization": "Bearer not.a.token"})
        assert bad.status_code == 401

        # 过期 token（直接签发负有效期）
        expired_token = create_token({"sub": "x"}, SECRET, expires_seconds=-5)
        old = await client.get("/security/me", headers={"Authorization": f"Bearer {expired_token}"})
        assert old.status_code == 401
    print("  签发 / 回读 / 401 x3 全部正确")
    print("  ✅ 通过")


async def test_rate_limit_endpoint():
    print("\nTest 10: 限流端点（5 次/10s -> 第 6 次 429 + Retry-After）")
    get_limiter().reset()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        for i in range(5):
            r = await client.get("/security/limited")
            assert r.status_code == 200, f"第 {i + 1} 次不应被限流"

        blocked = await client.get("/security/limited")
        assert blocked.status_code == 429
        assert "retry-after" in {k.lower() for k in blocked.headers}
        assert int(blocked.headers["retry-after"]) >= 1
    get_limiter().reset()
    print(f"  第 6 次被拒，Retry-After={blocked.headers['retry-after']}s")
    print("  ✅ 通过")


async def test_request_id_middleware():
    print("\nTest 11: 请求 ID 中间件（生成 / 上游透传）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        r = await client.get("/security/limited")
        assert r.status_code == 200
        gen_id = r.headers.get("x-request-id")
        assert gen_id and len(gen_id) == 16

        r2 = await client.get("/security/limited", headers={"X-Request-ID": "trace-abc-123"})
        assert r2.headers.get("x-request-id") == "trace-abc-123"
    get_limiter().reset()
    print(f"  自动生成 {gen_id}，上游传入原样透传")
    print("  ✅ 通过")


async def main():
    print("🚀 安全与限流模块测试开始")
    try:
        test_jwt_round_trip()
        test_jwt_expired()
        test_jwt_tampered()
        test_jwt_invalid()
        await test_limiter_memory()
        await test_limiter_redis()
        await test_limiter_redis_fallback()
        await test_api_key_flow()
        await test_jwt_flow()
        await test_rate_limit_endpoint()
        await test_request_id_middleware()
        print("\n" + "=" * 60)
        print("🎉 所有安全与限流模块测试通过！（11 项）")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
