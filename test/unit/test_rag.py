# @Description : RAG 模块单元测试（文档 CRUD / 分块 / 伪向量检索 / 问答 mock LLM / 权限隔离 / SSE 流式）
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock

import httpx
from fastapi import FastAPI
from tortoise import Tortoise

from src.faster.routers.rag.models import KnowledgeChunk, KnowledgeDoc
from src.faster.routers.rag.routers import rag_router
from src.faster.routers.users.models import User
from src.my_tools.password_tool import hash_password

TORTOISE_CONFIG = {
    "connections": {"default": "sqlite://:memory:"},
    "apps": {
        "users": {"models": ["src.faster.routers.users.models"], "default_connection": "default"},
        "rag": {"models": ["src.faster.routers.rag.models"], "default_connection": "default"},
    },
}


def build_app() -> FastAPI:
    """构建最小可运行的 FastAPI app，并挂载一个 mock LLM 的 RAG service。"""
    from src.my_tools.llm_tools import LLMMessage
    from src.my_tools.rag_tools import InMemoryVectorStore, RagService, RagServiceConfig

    app = FastAPI()

    # 构造一个可工作的伪 LLM gateway，仅 mock chat/stream_chat/embeddings
    mock_llm = MagicMock()
    # 默认有 provider（走真实 chat，embeddings 无实现则降级伪向量）
    mock_llm._providers = {"mock": MagicMock()}

    async def _fake_chat(messages, **kwargs):
        from src.my_tools.llm_tools.gateway import LLMResponse, LLMUsage

        q = messages[-1].content if isinstance(messages[-1], LLMMessage) else messages[-1].get("content", "")
        return LLMResponse(
            content=f"mock answer for: {q[:30]}",
            model="mock-model",
            provider="mock",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            latency_ms=0.1,
        )

    async def _fake_stream(messages, **kwargs):
        for token in ["Hello", ", ", "world", "!"]:
            yield token

    mock_llm.chat = AsyncMock(side_effect=_fake_chat)
    mock_llm.stream_chat = _fake_stream
    # embeddings 抛错 → 触发降级伪向量
    mock_llm.embeddings = AsyncMock(side_effect=RuntimeError("no real embedding"))

    rag_svc = RagService(
        llm_gateway=mock_llm,
        vector_store=InMemoryVectorStore(),
        config=RagServiceConfig(default_top_k=3),
    )
    app.state.llm = mock_llm
    app.state.rag = rag_svc
    app.include_router(rag_router)
    return app


async def _create_user(username: str, role: str = "user") -> tuple[User, str]:
    pwd = "password123"
    user = await User.create(username=username, password_hash=hash_password(pwd), role=role, is_active=True)
    return user, pwd


async def _token(username: str) -> str:
    from src.my_tools.security_tools.jwt_tools import create_token
    from src.settings import SECURITY_CONFIG

    user = await User.get(username=username)
    return create_token(
        {"sub": user.username, "role": user.role, "uid": user.id},
        SECURITY_CONFIG["jwt_secret"],
        algorithm=SECURITY_CONFIG["jwt_algorithm"],
        expires_seconds=SECURITY_CONFIG["jwt_expire_seconds"],
    )


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def _create_doc(client: httpx.AsyncClient, tok: str, title: str, content: str) -> dict:
    r = await client.post("/rag/docs", json={"title": title, "content": content}, headers=_auth(tok))
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
#  1. 文档 CRUD + 自动分块
# ---------------------------------------------------------------------------
async def test_doc_crud():
    print("\nTest 1: 创建文档（自动分块+伪向量入库）/ 查询 / 删除")
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _create_user("alice")
        tok = await _token("alice")

        long_text = "这是一段用于测试 RAG 模块的知识库文档。" * 30 + "我们来看看向量检索是否能工作。" * 20
        doc = await _create_doc(client, tok, "测试文档", long_text)
        assert doc["title"] == "测试文档"
        assert doc["owner_id"] > 0
        assert doc["chunk_count"] >= 2
        assert len(doc["content"]) == len(long_text)

        # 列表
        r = await client.get("/rag/docs", headers=_auth(tok))
        assert r.status_code == 200
        assert r.json()["total"] == 1

        # 详情
        r2 = await client.get(f"/rag/docs/{doc['id']}", headers=_auth(tok))
        assert r2.status_code == 200
        assert r2.json()["content"] == long_text

        # stats
        rs = await client.get("/rag/stats", headers=_auth(tok))
        assert rs.status_code == 200
        stats = rs.json()
        assert stats["total_docs"] == 1
        assert stats["total_chunks"] == doc["chunk_count"]
        assert stats["index_size"] == doc["chunk_count"]
        assert stats["has_llm"] is True
        assert stats["fallback_embedding"] is True  # mock embedding 报错 → 降级伪向量

        # 删除
        r3 = await client.delete(f"/rag/docs/{doc['id']}", headers=_auth(tok))
        assert r3.status_code == 204
        r4 = await client.get(f"/rag/docs/{doc['id']}", headers=_auth(tok))
        assert r4.status_code == 404
    print("  创建 201 / 列表/详情 200 / 删除 204 / 删除后 404 / stats 正确")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  2. 权限隔离
# ---------------------------------------------------------------------------
async def test_access_control():
    print("\nTest 2: 权限隔离（普通用户不可见他人文档，越权返回 404）")
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _create_user("bob")
        bob_tok = await _token("bob")
        b_doc = await _create_doc(client, bob_tok, "Bob's doc", "Bob secret content")

        await _create_user("charlie")
        c_tok = await _token("charlie")

        r = await client.get(f"/rag/docs/{b_doc['id']}", headers=_auth(c_tok))
        assert r.status_code == 404
        r2 = await client.put(
            f"/rag/docs/{b_doc['id']}",
            json={"title": "hacked"},
            headers=_auth(c_tok),
        )
        assert r2.status_code == 404
        r3 = await client.delete(f"/rag/docs/{b_doc['id']}", headers=_auth(c_tok))
        assert r3.status_code == 404

        # 检索只能看到自己的（charlie 无文档，检索应 0 hits）
        r4 = await client.post(
            "/rag/search",
            json={"query": "Bob secret"},
            headers=_auth(c_tok),
        )
        assert r4.status_code == 200
        assert len(r4.json()["hits"]) == 0

        # bob 自己检索能命中
        r5 = await client.post(
            "/rag/search",
            json={"query": "Bob secret"},
            headers=_auth(bob_tok),
        )
        assert r5.status_code == 200
        assert len(r5.json()["hits"]) >= 1
    print("  越权 GET/PUT/DELETE/search 全部正确隔离")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  3. 检索 + 普通问答
# ---------------------------------------------------------------------------
async def test_search_and_chat():
    print("\nTest 3: 向量检索 + LLM 问答（非流式）")
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _create_user("dave")
        tok = await _token("dave")
        await _create_doc(client, tok, "Python 介绍", "Python 是一种高级编程语言，广泛用于 AI 和 Web 开发。")
        await _create_doc(client, tok, "FastAPI 介绍", "FastAPI 是一个现代化的 Python Web 框架，性能极高，类型提示友好。")

        # 检索到 Python 文档
        r = await client.post("/rag/search", json={"query": "什么是 Python"}, headers=_auth(tok))
        assert r.status_code == 200
        body = r.json()
        assert len(body["hits"]) >= 1
        assert any("Python" in h["content"] for h in body["hits"])
        assert body["fallback_embedding"] is True  # 伪向量模式

        # 普通问答
        r2 = await client.post("/rag/chat", json={"query": "什么是 Python", "stream": False}, headers=_auth(tok))
        assert r2.status_code == 200
        data = r2.json()
        assert "mock answer" in data["answer"]
        assert data["provider"] == "mock"
        assert len(data["hits"]) >= 1
    print("  检索命中文档 / 非流式问答返回 mock 回答 + hits")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  4. SSE 流式问答
# ---------------------------------------------------------------------------
async def test_stream_chat():
    print("\nTest 4: SSE 流式问答（解析事件流，含 retrieval/delta/done）")
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _create_user("eve")
        tok = await _token("eve")
        await _create_doc(client, tok, "Demo", "hello world content for streaming test")

        async with client.stream(
            "POST",
            "/rag/chat",
            json={"query": "hello", "stream": True},
            headers=_auth(tok),
        ) as resp:
            assert resp.status_code == 200
            text = (await resp.aread()).decode("utf-8")

        events: dict[str, list] = {"retrieval": [], "delta": [], "done": [], "error": []}
        for chunk in text.split("\n\n"):
            chunk = chunk.strip()
            if not chunk.startswith("event:"):
                continue
            lines = chunk.splitlines()
            ev_name = lines[0].split(":", 1)[1].strip()
            data_lines = [ln[len("data:") :] for ln in lines[1:] if ln.startswith("data:")]
            data_str = "\n".join(d_lines.strip() for d_lines in data_lines)
            try:
                payload = json.loads(data_str)
            except Exception:
                continue
            events.setdefault(ev_name, []).append(payload)

        assert events["retrieval"], "应至少有一个 retrieval 事件"
        assert events["delta"], "应至少有一个 delta 事件"
        assert events["done"], "应有 done 事件"
        # delta 内容拼起来
        deltas = "".join(d.get("content", "") for d in events["delta"])
        assert "Hello" in deltas and "world" in deltas
    print("  SSE 流 retrieval/delta/done 事件齐全，内容正确")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  5. 参数校验 & 认证
# ---------------------------------------------------------------------------
async def test_validation():
    print("\nTest 5: 参数校验（空字段 422 / 无 token 401 / 无 LLM 时 chat 503）")
    app = build_app()
    # 把 LLM 移除，验证 chat 503
    app.state.rag._llm = MagicMock()
    app.state.rag._llm._providers = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _create_user("frank")
        tok = await _token("frank")
        await _create_doc(client, tok, "doc", "content")

        # 无 token 401
        r0 = await client.get("/rag/docs")
        assert r0.status_code == 401

        # 空 content 422
        r1 = await client.post("/rag/docs", json={"title": "x", "content": ""}, headers=_auth(tok))
        assert r1.status_code == 422

        # 空 query 422
        r2 = await client.post("/rag/search", json={"query": ""}, headers=_auth(tok))
        assert r2.status_code == 422

        # top_k 越界 422
        r3 = await client.post("/rag/search", json={"query": "x", "top_k": 100}, headers=_auth(tok))
        assert r3.status_code == 422

        # 无 LLM 时 chat 返回 503
        r4 = await client.post("/rag/chat", json={"query": "hi", "stream": False}, headers=_auth(tok))
        assert r4.status_code == 503
    print("  401/422/503 全部符合预期")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  6. 分块函数独立测试
# ---------------------------------------------------------------------------
async def test_chunking():
    print("\nTest 6: chunk_text 工具函数（分块大小/重叠/空值处理）")
    from src.my_tools.rag_tools import chunk_text

    assert chunk_text("") == []
    assert chunk_text("   ") == []
    text = "。".join([f"句子{i}" for i in range(50)])
    chunks = chunk_text(text, chunk_size=50, chunk_overlap=10)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 60  # 允许小量超出
    print(f"  50 句切分为 {len(chunks)} 块，均在长度限制内")
    print("  ✅ 通过")


# ---------------------------------------------------------------------------
#  main
# ---------------------------------------------------------------------------
async def main():
    print("🚀 RAG 模块测试开始")
    try:
        await Tortoise.init(config=TORTOISE_CONFIG)
        await Tortoise.generate_schemas(safe=True)
        try:
            await test_chunking()
            await test_doc_crud()
            await test_access_control()
            await test_search_and_chat()
            await test_stream_chat()
            await test_validation()
        finally:
            await Tortoise.close_connections()
        print("\n" + "=" * 60)
        print("🎉 所有 RAG 模块测试通过！（6 项）")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
