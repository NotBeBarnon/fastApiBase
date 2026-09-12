# @Description : 里程碑 10 单元测试（资源 CRUD / 权限隔离 / 分页 / 过滤 / 排序 / 管理员，SQLite 内存库）
from __future__ import annotations

import asyncio
import sys

import httpx
from fastapi import FastAPI
from tortoise import Tortoise

from src.faster.routers.resource.routers import resource_router
from src.faster.routers.users.models import User
from src.my_tools.password_tool import hash_password

TORTOISE_CONFIG = {
    "connections": {"default": "sqlite://:memory:"},
    "apps": {
        "users": {"models": ["src.faster.routers.users.models"], "default_connection": "default"},
        "resource": {"models": ["src.faster.routers.resource.models"], "default_connection": "default"},
    },
}


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(resource_router)
    return app


async def _create_user(username: str, role: str = "user") -> tuple[User, str]:
    """创建用户并返回 (User, 明文密码)"""
    pwd = "password123"
    user = await User.create(
        username=username,
        password_hash=hash_password(pwd),
        role=role,
        is_active=True,
    )
    return user, pwd


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: httpx.AsyncClient, username: str, password: str) -> str:
    """走 /user/login 拿 token（需要把 user_router 也挂到 app 上）"""
    # 测试 resource 模块时不依赖 users 路由，直接签发 token
    from src.my_tools.security_tools.jwt_tools import create_token
    from src.settings import SECURITY_CONFIG

    user = await User.get(username=username)
    token = create_token(
        {"sub": user.username, "role": user.role, "uid": user.id},
        SECURITY_CONFIG["jwt_secret"],
        algorithm=SECURITY_CONFIG["jwt_algorithm"],
        expires_seconds=SECURITY_CONFIG["jwt_expire_seconds"],
    )
    return token


async def _create_beam(client: httpx.AsyncClient, token: str, name: str, beam_type: int = 1) -> dict:
    r = await client.post(
        "/resource/beams",
        json={"name": name, "type": beam_type},
        headers=_auth_headers(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
#  1. 创建与基本查询
# ---------------------------------------------------------------------------
async def test_create_and_get():
    print("\nTest 1: 创建波束 + 根据 ID 查询（owner 自动绑定）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        user, pwd = await _create_user("alice")
        token = await _login(client, "alice", pwd)

        beam = await _create_beam(client, token, "Beam-A", 1)
        assert beam["name"] == "Beam-A"
        assert beam["type"] == 1
        assert beam["owner_id"] == user.id
        assert beam["is_active"] is True
        assert "id" in beam

        # GET 单个
        r = await client.get(f"/resource/beams/{beam['id']}", headers=_auth_headers(token))
        assert r.status_code == 200
        assert r.json()["name"] == "Beam-A"

        # 无 token 应 401
        r2 = await client.get(f"/resource/beams/{beam['id']}")
        assert r2.status_code == 401
    print("  创建 201 / 查询 200 / 无 token 401，owner 正确绑定")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  2. 越权访问（404 防止枚举探测）
# ---------------------------------------------------------------------------
async def test_access_control():
    print("\nTest 2: 权限隔离（用户只能看自己的，越权返回 404 而非 403）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        await _create_user("bob")
        bob_token = await _login(client, "bob", "password123")
        bob_beam = await _create_beam(client, bob_token, "Bob-Beam", 1)

        await _create_user("charlie")
        charlie_token = await _login(client, "charlie", "password123")

        # charlie 查 bob 的 beam -> 404
        r = await client.get(f"/resource/beams/{bob_beam['id']}", headers=_auth_headers(charlie_token))
        assert r.status_code == 404

        # charlie 改 bob 的 beam -> 404
        r2 = await client.put(
            f"/resource/beams/{bob_beam['id']}",
            json={"name": "hacked"},
            headers=_auth_headers(charlie_token),
        )
        assert r2.status_code == 404

        # charlie 删 bob 的 beam -> 404
        r3 = await client.delete(f"/resource/beams/{bob_beam['id']}", headers=_auth_headers(charlie_token))
        assert r3.status_code == 404

        # bob 的列表里只有 1 条
        r4 = await client.get("/resource/beams", headers=_auth_headers(bob_token))
        assert r4.status_code == 200
        assert r4.json()["total"] == 1

        # charlie 的列表 0 条
        r5 = await client.get("/resource/beams", headers=_auth_headers(charlie_token))
        assert r5.status_code == 200
        assert r5.json()["total"] == 0
    print("  越权 GET/PUT/DELETE 均 404，列表各自独立")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  3. 更新与删除
# ---------------------------------------------------------------------------
async def test_update_and_delete():
    print("\nTest 3: 更新（部分更新）+ 删除（204）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        await _create_user("dave")
        token = await _login(client, "dave", "password123")
        beam = await _create_beam(client, token, "Old-Name", 1)

        # 部分更新：只改 name
        r = await client.put(
            f"/resource/beams/{beam['id']}",
            json={"name": "New-Name"},
            headers=_auth_headers(token),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "New-Name"
        assert body["type"] == 1  # type 没变

        # 改 type
        r2 = await client.put(
            f"/resource/beams/{beam['id']}",
            json={"type": 2},
            headers=_auth_headers(token),
        )
        assert r2.status_code == 200
        assert r2.json()["type"] == 2

        # 删除
        r3 = await client.delete(f"/resource/beams/{beam['id']}", headers=_auth_headers(token))
        assert r3.status_code == 204

        # 删除后查不到
        r4 = await client.get(f"/resource/beams/{beam['id']}", headers=_auth_headers(token))
        assert r4.status_code == 404
    print("  部分更新 200 / 删除 204 / 删除后 404")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  4. 分页 + 过滤 + 排序
# ---------------------------------------------------------------------------
async def test_pagination_filter_sort():
    print("\nTest 4: 分页 / 类型过滤 / 名称模糊搜索 / 排序")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        await _create_user("eve")
        token = await _login(client, "eve", "password123")

        # 创建 6 条：3 KA, 3 X
        for i in range(3):
            await _create_beam(client, token, f"Alpha-{i}", 1)
        for i in range(3):
            await _create_beam(client, token, f"Beta-{i}", 2)

        # 全量总数
        r = await client.get("/resource/beams", headers=_auth_headers(token))
        assert r.status_code == 200
        assert r.json()["total"] == 6

        # 分页：第 1 页 2 条
        r2 = await client.get("/resource/beams", params={"page": 1, "page_size": 2}, headers=_auth_headers(token))
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["total"] == 6
        assert data2["page"] == 1
        assert data2["page_size"] == 2
        assert len(data2["items"]) == 2

        # 第 3 页也应有 2 条
        r3 = await client.get("/resource/beams", params={"page": 3, "page_size": 2}, headers=_auth_headers(token))
        assert r3.status_code == 200
        assert len(r3.json()["items"]) == 2

        # 类型过滤：KA 类型应 3 条
        r4 = await client.get("/resource/beams", params={"beam_type": 1}, headers=_auth_headers(token))
        assert r4.status_code == 200
        assert r4.json()["total"] == 3

        # 名称模糊搜索：Alpha 应 3 条
        r5 = await client.get("/resource/beams", params={"name_like": "Alpha"}, headers=_auth_headers(token))
        assert r5.status_code == 200
        assert r5.json()["total"] == 3

        # 排序：按 name 升序
        r6 = await client.get("/resource/beams", params={"sort": "name", "page_size": 100}, headers=_auth_headers(token))
        assert r6.status_code == 200
        names = [b["name"] for b in r6.json()["items"]]
        assert names == sorted(names)

        # 排序：按 name 降序
        r7 = await client.get("/resource/beams", params={"sort": "-name", "page_size": 100}, headers=_auth_headers(token))
        assert r7.status_code == 200
        names_desc = [b["name"] for b in r7.json()["items"]]
        assert names_desc == sorted(names_desc, reverse=True)

        # 非法排序字段回退到默认
        r8 = await client.get("/resource/beams", params={"sort": "password_hash"}, headers=_auth_headers(token))
        assert r8.status_code == 200  # 不报错，回退默认排序
    print("  分页 / 类型过滤 / 模糊搜索 / 升序降序 / 非法字段回退 全部正确")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  5. admin 全量查询
# ---------------------------------------------------------------------------
async def test_admin_endpoint():
    print("\nTest 5: 管理员端点（admin 可看全部 / 普通用户 403 / owner_id 过滤）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        # 两个普通用户各建 2 条
        await _create_user("user_a")
        tok_a = await _login(client, "user_a", "password123")
        for i in range(2):
            await _create_beam(client, tok_a, f"A-{i}", 1)

        await _create_user("user_b")
        tok_b = await _login(client, "user_b", "password123")
        for i in range(2):
            await _create_beam(client, tok_b, f"B-{i}", 2)

        # 普通用户访问 /admin/beams -> 403
        r = await client.get("/resource/admin/beams", headers=_auth_headers(tok_a))
        assert r.status_code == 403

        # admin 可见全部（含前面测试遗留的数据，这里只验证不少于 4）
        await _create_user("admin_user", role="admin")
        admin_token = await _login(client, "admin_user", "password123")
        r2 = await client.get("/resource/admin/beams", headers=_auth_headers(admin_token))
        assert r2.status_code == 200
        assert r2.json()["total"] >= 4

        # admin 按 owner_id 过滤：user_a 的 2 条
        user_a_obj = await User.get(username="user_a")
        r3 = await client.get(
            "/resource/admin/beams",
            params={"owner_id": user_a_obj.id},
            headers=_auth_headers(admin_token),
        )
        assert r3.status_code == 200
        assert r3.json()["total"] == 2
        assert all(b["owner_id"] == user_a_obj.id for b in r3.json()["items"])
    print("  普通用户 403 / admin 全量 / owner_id 过滤 2 条")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  6. 参数校验
# ---------------------------------------------------------------------------
async def test_validation():
    print("\nTest 6: 参数校验（空名称 422 / 非法类型 422 / 分页越界修正）")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app()), base_url="http://test") as client:
        await _create_user("frank")
        token = await _login(client, "frank", "password123")

        # 空名称
        r = await client.post("/resource/beams", json={"name": "", "type": 1}, headers=_auth_headers(token))
        assert r.status_code == 422

        # 非法 type 值
        r2 = await client.post("/resource/beams", json={"name": "ok", "type": 99}, headers=_auth_headers(token))
        assert r2.status_code == 422

        # page_size 超 100 应该返回 422（由 Query ge/le 校验）
        r3 = await client.get("/resource/beams", params={"page_size": 200}, headers=_auth_headers(token))
        assert r3.status_code == 422

        # page < 1 也 422
        r4 = await client.get("/resource/beams", params={"page": 0}, headers=_auth_headers(token))
        assert r4.status_code == 422
    print("  空名称 422 / 非法 type 422 / 分页越界 422")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  main
# ---------------------------------------------------------------------------
async def main():
    print("🚀 资源模块测试开始")
    try:
        await Tortoise.init(config=TORTOISE_CONFIG)
        await Tortoise.generate_schemas(safe=True)
        try:
            await test_create_and_get()
            await test_access_control()
            await test_update_and_delete()
            await test_pagination_filter_sort()
            await test_admin_endpoint()
            await test_validation()
        finally:
            await Tortoise.close_connections()
        print("\n" + "=" * 60)
        print("🎉 所有资源模块测试通过！（6 项）")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
