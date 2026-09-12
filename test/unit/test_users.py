# @Description : 里程碑 9 单元测试（密码工具 / 用户注册登录改密 / 角色鉴权，SQLite 内存库）
from __future__ import annotations

import asyncio
import sys

import httpx
from fastapi import FastAPI
from tortoise import Tortoise

from src.faster.routers.users.routers import user_router
from src.my_tools.password_tool import hash_password, verify_password

MODELS = {"models": ["src.faster.routers.users.models"]}


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(user_router)
    return app


async def _register(client, username: str, password: str = "password123"):
    return await client.post(
        "/user/register", json={"username": username, "password": password, "password_again": password}
    )


async def _login(client, username: str, password: str) -> dict:
    r = await client.post("/user/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
#  密码工具
# ---------------------------------------------------------------------------
def test_password_tool():
    print("\nTest 1: PBKDF2 密码哈希（格式 / 校验 / 盐唯一 / 非法存储）")
    stored = hash_password("s3cret-pwd")
    assert stored.startswith("pbkdf2_sha256$100000$")
    assert len(stored.split("$")) == 4

    assert verify_password("s3cret-pwd", stored) is True
    assert verify_password("wrong-pwd", stored) is False

    # 相同密码不同盐 -> 不同哈希，且都能验证通过
    other = hash_password("s3cret-pwd")
    assert other != stored
    assert verify_password("s3cret-pwd", other) is True

    # 非法存储格式 / 明文不匹配
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "md5$1$aa$bb") is False
    print("  哈希格式 / 正误校验 / 盐唯一 / 非法格式全部正确")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  注册 / 登录 / me / 改密码
# ---------------------------------------------------------------------------
async def test_register_flow():
    print("\nTest 2: 注册（成功 201 / 重名 409 / 两次密码不一致 422 / 非法用户名 422）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        r = await _register(client, "alice")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["username"] == "alice" and body["role"] == "user" and body["is_active"] is True
        assert "password_hash" not in body and "password" not in body

        dup = await _register(client, "alice")
        assert dup.status_code == 409

        mismatch = await client.post(
            "/user/register",
            json={"username": "bob", "password": "password123", "password_again": "different1"},
        )
        assert mismatch.status_code == 422

        bad_name = await client.post(
            "/user/register",
            json={"username": "bad name!", "password": "password123", "password_again": "password123"},
        )
        assert bad_name.status_code == 422
    print("  201 / 409 / 422 x2，密码哈希不出现在响应")
    print("  ✅ 通过")


async def test_login_and_me():
    print("\nTest 3: 登录签发 JWT -> /user/me 回读（错密码 401 / 无 token 401）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        await _register(client, "carol")

        wrong = await client.post("/user/login", json={"username": "carol", "password": "wrong-pass"})
        assert wrong.status_code == 401

        no_user = await client.post("/user/login", json={"username": "ghost", "password": "whatever1"})
        assert no_user.status_code == 401

        token = await _login(client, "carol", "password123")
        assert token["token_type"] == "Bearer" and token["expires_in"] > 0

        me = await client.get("/user/me", headers={"Authorization": f"Bearer {token['access_token']}"})
        assert me.status_code == 200
        assert me.json()["username"] == "carol"

        no_token = await client.get("/user/me")
        assert no_token.status_code == 401
    print("  登录 200 / 错密码 401 / 不存在 401 / me 回读 200 / 无 token 401")
    print("  ✅ 通过")


async def test_change_password():
    print("\nTest 4: 修改密码（旧密码错误 401 -> 修改成功 -> 新旧密码登录验证）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        await _register(client, "dave")
        token = (await _login(client, "dave", "password123"))["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        wrong_old = await client.put(
            "/user/password",
            json={"old_password": "bad-old-pwd", "new_password": "newpassword9"},
            headers=headers,
        )
        assert wrong_old.status_code == 401

        ok = await client.put(
            "/user/password",
            json={"old_password": "password123", "new_password": "newpassword9"},
            headers=headers,
        )
        assert ok.status_code == 200

        old_login = await client.post("/user/login", json={"username": "dave", "password": "password123"})
        assert old_login.status_code == 401
        new_login = await client.post("/user/login", json={"username": "dave", "password": "newpassword9"})
        assert new_login.status_code == 200
    print("  401 -> 200 -> 旧密码失效 / 新密码有效")
    print("  ✅ 通过")


async def test_admin_role():
    print("\nTest 5: admin 角色控制（普通用户 403 / admin 200 分页列表）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        await _register(client, "normal_user")
        user_token = (await _login(client, "normal_user", "password123"))["access_token"]

        forbidden = await client.get(
            "/user/list", headers={"Authorization": f"Bearer {user_token}"}
        )
        assert forbidden.status_code == 403

        # 手工把一个用户提权为 admin（模拟管理员操作）
        from src.faster.routers.users.models import User

        target = await User.get(username="normal_user")
        target.role = "admin"
        await target.save(update_fields=["role"])

        admin_token = (await _login(client, "normal_user", "password123"))["access_token"]
        listed = await client.get(
            "/user/list", headers={"Authorization": f"Bearer {admin_token}"}, params={"page": 1, "page_size": 10}
        )
        assert listed.status_code == 200
        data = listed.json()
        assert data["total"] >= 1 and len(data["items"]) >= 1
        assert all("password_hash" not in item for item in data["items"])
    print(f"  普通用户 403，admin 200，total={data['total']}，无哈希泄露")
    print("  ✅ 通过")


async def main():
    print("🚀 用户体系模块测试开始")
    try:
        test_password_tool()
        await Tortoise.init(db_url="sqlite://:memory:", modules=MODELS)
        await Tortoise.generate_schemas(safe=True)
        try:
            await test_register_flow()
            await test_login_and_me()
            await test_change_password()
            await test_admin_role()
        finally:
            await Tortoise.close_connections()
        print("\n" + "=" * 60)
        print("🎉 所有用户体系模块测试通过！（5 项）")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
