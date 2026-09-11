# -*- coding: utf-8 -*-
# @Description : LLM 网关测试（用 mock HTTP 响应）
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.my_tools.llm_tools import LLMGateway, LLMConfig, LLMMessage, LLMResponse, LLMUsage, LLMProvider, ProviderType


def make_config() -> LLMConfig:
    """创建测试用配置"""
    return LLMConfig(
        default_provider="test-provider",
        fallback_providers=["backup-provider"],
        providers={
            "test-provider": LLMProvider(
                name="test-provider",
                type=ProviderType.OPENAI,
                base_url="https://test.api/v1",
                api_key="test-key-123",
                default_model="test-model",
                timeout=10,
                max_retries=1,
                priority=100,
                enabled=True,
            ),
            "backup-provider": LLMProvider(
                name="backup-provider",
                type=ProviderType.OPENAI,
                base_url="https://backup.api/v1",
                api_key="backup-key",
                default_model="backup-model",
                timeout=10,
                max_retries=0,
                priority=50,
                enabled=True,
            ),
        },
    )


def test_llm_message():
    """测试 LLMMessage 结构"""
    print("Test 1: LLMMessage")

    msg = LLMMessage(role="user", content="你好")
    d = msg.to_dict()
    assert d["role"] == "user"
    assert d["content"] == "你好"
    print("  ✅ 通过")


def test_config():
    """测试配置加载"""
    print("\nTest 2: 配置加载")

    cfg = make_config()
    assert cfg.default_provider == "test-provider"
    assert len(cfg.providers) == 2
    assert cfg.providers["test-provider"].type == ProviderType.OPENAI
    print("  ✅ 通过")


def test_gateway_init():
    """测试网关初始化"""
    print("\nTest 3: 网关初始化")

    cfg = make_config()
    gateway = LLMGateway(cfg)
    assert len(gateway._providers) == 2
    assert gateway.call_count == 0
    assert gateway.cost_total == 0.0
    print("  ✅ 通过")


async def test_chat_mock():
    """测试 chat 接口（mock 底层 provider 调用）"""
    print("\nTest 4: chat 接口（mock）")

    cfg = make_config()
    gateway = LLMGateway(cfg)

    expected = LLMResponse(
        content="你好！我是测试助手。",
        model="test-model",
        provider="test-provider",
        usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        finish_reason="stop",
        latency_ms=10.0,
    )

    # 直接 mock _chat_provider 方法
    async def mock_chat_provider(prov, **kw):
        return expected

    with patch.object(gateway, "_chat_provider", side_effect=mock_chat_provider):
        resp = await gateway.chat([
            LLMMessage(role="system", content="你是助手"),
            LLMMessage(role="user", content="你好"),
        ])

    assert isinstance(resp, LLMResponse)
    assert "你好" in resp.content
    assert resp.model == "test-model"
    assert resp.usage.total_tokens == 15
    assert resp.finish_reason == "stop"
    print(f"  content: {resp.content[:30]}...")
    print(f"  tokens: {resp.usage.total_tokens}")
    print("  ✅ 通过")


async def test_stream_chat_mock():
    """测试流式 chat 接口（mock 流式生成器）"""
    print("\nTest 5: stream_chat 接口（mock）")

    cfg = make_config()
    gateway = LLMGateway(cfg)

    async def mock_stream(prov, messages, **kw):
        for char in ["你", "好", "！"]:
            yield char

    with patch.object(gateway, "_stream_chat_provider", side_effect=mock_stream):
        chunks = []
        async for chunk in gateway.stream_chat([
            LLMMessage(role="user", content="你好"),
        ]):
            chunks.append(chunk)

    full = "".join(chunks)
    assert full == "你好！"
    print(f"  收到 {len(chunks)} 个 chunk: {full}")
    print("  ✅ 通过")


async def test_embeddings_mock():
    """测试 embeddings 接口（mock）"""
    print("\nTest 6: embeddings 接口（mock）")

    cfg = make_config()
    gateway = LLMGateway(cfg)

    expected = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    async def mock_embeddings(prov, texts, **kw):
        return expected

    with patch.object(gateway, "_embeddings_provider", side_effect=mock_embeddings):
        result = await gateway.embeddings(["你好", "世界"])

    assert len(result) == 2
    assert len(result[0]) == 3
    assert result[0] == [0.1, 0.2, 0.3]
    print(f"  生成 {len(result)} 个 embedding，维度 {len(result[0])}")
    print("  ✅ 通过")


async def test_stats():
    """测试统计信息"""
    print("\nTest 7: 统计信息")

    cfg = make_config()
    gateway = LLMGateway(cfg)
    stats = gateway.get_stats()

    assert stats["call_count"] == 0
    assert stats["cost_total_usd"] == 0
    assert "test-provider" in stats["enabled_providers"]
    print(f"  providers: {list(stats['enabled_providers'].keys())}")
    print("  ✅ 通过")


async def test_health_check():
    """测试健康检查"""
    print("\nTest 8: 健康检查")

    cfg = make_config()
    gateway = LLMGateway(cfg)
    health = gateway.health_check()

    assert "test-provider" in health
    assert health["test-provider"]["enabled"] is True
    assert health["test-provider"]["has_api_key"] is True
    print(f"  {len(health)} providers checked")
    print("  ✅ 通过")


async def test_no_providers():
    """测试无 provider 场景"""
    print("\nTest 9: 无 provider 场景")

    cfg = LLMConfig(default_provider="", providers={})
    gateway = LLMGateway(cfg)
    assert len(gateway._providers) == 0
    stats = gateway.get_stats()
    assert stats["call_count"] == 0
    print("  ✅ 通过")


async def test_dict_messages():
    """测试 dict 格式消息（兼容直接传 dict）"""
    print("\nTest 10: dict 格式消息")

    cfg = make_config()
    gateway = LLMGateway(cfg)

    expected = LLMResponse(content="hi", model="test-model")

    async def mock_chat(prov, messages, **kw):
        return expected

    with patch.object(gateway, "_chat_provider", side_effect=mock_chat):
        resp = await gateway.chat([
            {"role": "user", "content": "hello"},
        ])

    assert resp.content == "hi"
    print("  ✅ 通过")


async def main():
    print("\n🚀 LLM 网关测试开始\n")
    try:
        test_llm_message()
        test_config()
        test_gateway_init()
        await test_chat_mock()
        await test_stream_chat_mock()
        await test_embeddings_mock()
        await test_stats()
        await test_health_check()
        await test_no_providers()
        await test_dict_messages()
        print("\n" + "=" * 60)
        print("🎉 所有 LLM 网关测试通过！")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
