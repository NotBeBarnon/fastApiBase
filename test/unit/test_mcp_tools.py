# -*- coding: utf-8 -*-
# @Description : MCP 功能验证脚本（不启动服务，直接测核心逻辑）
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 确保 src 可被导入
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.my_tools.mcp_tools import ToolRegistry, mcp_tool
from src.my_tools.mcp_tools.examples import register_example_tools
from src.my_tools.mcp_tools.server import MCPServer


async def test_registry():
    """测试 ToolRegistry 基本功能"""
    print("=" * 60)
    print("Test 1: ToolRegistry 注册与查询")
    print("=" * 60)

    registry = ToolRegistry.get_instance()
    register_example_tools()

    tools = registry.list_tools()
    print(f"  已注册工具数: {len(tools)}")
    for t in tools:
        print(f"  - {t.name}: {t.description[:50]}")

    assert len(tools) >= 3, "示例工具应该至少有 3 个"
    print("  ✅ 通过")


async def test_openai_format():
    """测试 OpenAI Function Calling 格式导出"""
    print("\n" + "=" * 60)
    print("Test 2: OpenAI Function Calling 格式导出")
    print("=" * 60)

    registry = ToolRegistry.get_instance()
    functions = registry.to_openai_functions()

    print(f"  导出函数数: {len(functions)}")
    for f in functions:
        fn = f["function"]
        print(f"  - {fn['name']}")
        print(f"    参数: {list(fn['parameters']['properties'].keys())}")
        print(f"    必填: {fn['parameters']['required']}")

    # 验证结构
    for f in functions:
        assert f["type"] == "function"
        assert "function" in f
        assert "name" in f["function"]
        assert "parameters" in f["function"]
        assert f["function"]["parameters"]["type"] == "object"
    print("  ✅ 通过")


async def test_mcp_format():
    """测试 MCP 工具格式导出"""
    print("\n" + "=" * 60)
    print("Test 3: MCP 工具格式导出")
    print("=" * 60)

    registry = ToolRegistry.get_instance()
    mcp_tools = registry.to_mcp_tools()

    print(f"  导出工具数: {len(mcp_tools)}")
    for t in mcp_tools:
        print(f"  - {t['name']}")
        assert "inputSchema" in t
        assert t["inputSchema"]["type"] == "object"

    print("  ✅ 通过")


async def test_tool_call():
    """测试工具调用"""
    print("\n" + "=" * 60)
    print("Test 4: 工具调用")
    print("=" * 60)

    registry = ToolRegistry.get_instance()

    # 测试 echo
    result = await registry.call_tool("echo", {"message": "hello mcp"})
    print(f"  echo('hello mcp') => {result}")
    assert result["content"][0]["text"] == "hello mcp"

    # 测试 random_number
    result = await registry.call_tool("random_number", {"min_value": 10, "max_value": 20})
    print(f"  random_number(10,20) => {result}")
    val = json.loads(result["content"][0]["text"])
    assert 10 <= val["random"] <= 20

    # 测试参数缺失
    result = await registry.call_tool("echo", {})
    print(f"  echo(缺少参数) => {result}")
    assert result.get("isError") is True

    # 测试不存在的工具
    result = await registry.call_tool("nonexistent_tool", {})
    print(f"  nonexistent_tool => {result}")
    assert result.get("isError") is True

    print("  ✅ 通过")


async def test_mcp_server():
    """测试 MCP Server JSON-RPC 处理"""
    print("\n" + "=" * 60)
    print("Test 5: MCP Server JSON-RPC")
    print("=" * 60)

    server = MCPServer(name="test-server", version="1.0.0")

    # initialize
    resp = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "test-client", "version": "1.0"}},
    })
    print(f"  initialize => protocolVersion: {resp['result']['protocolVersion']}")
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in resp["result"]["capabilities"]

    # tools/list
    resp = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    })
    print(f"  tools/list => {len(resp['result']['tools'])} tools")
    assert len(resp["result"]["tools"]) >= 3

    # tools/call
    resp = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": "mcp-works"}},
    })
    print(f"  tools/call(echo) => {resp['result']['content'][0]['text']}")
    assert resp["result"]["content"][0]["text"] == "mcp-works"

    # ping
    resp = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "ping",
        "params": {},
    })
    print(f"  ping => {resp['result']}")

    # 不存在的方法
    resp = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 999,
        "method": "nonexistent",
        "params": {},
    })
    print(f"  nonexistent method => error code: {resp['error']['code']}")
    assert resp["error"]["code"] == -32601

    print("  ✅ 通过")


async def test_custom_tool():
    """测试自定义 @mcp_tool 装饰器"""
    print("\n" + "=" * 60)
    print("Test 6: 自定义 @mcp_tool 装饰器")
    print("=" * 60)

    @mcp_tool(description="计算两个数的和", tags=["math"])
    async def add(a: int, b: int = 0) -> int:
        """
        计算两个整数的和。

        Args:
            a: 第一个加数
            b: 第二个加数（默认为 0）

        Returns:
            两数之和
        """
        return a + b

    registry = ToolRegistry.get_instance()
    tool = registry.register(add)
    print(f"  注册工具: {tool.name}")
    print(f"  参数: {[p.name for p in tool.parameters]}")

    # 调用
    result = await registry.call_tool("add", {"a": 3, "b": 5})
    print(f"  add(3, 5) => {result['content'][0]['text']}")
    assert result["content"][0]["text"] == "8"

    # 测试默认参数
    result = await registry.call_tool("add", {"a": 10})
    print(f"  add(10) => {result['content'][0]['text']}")
    assert result["content"][0]["text"] == "10"

    print("  ✅ 通过")


async def main():
    print("\n🚀 MCP 功能验证开始\n")
    try:
        await test_registry()
        await test_openai_format()
        await test_mcp_format()
        await test_tool_call()
        await test_mcp_server()
        await test_custom_tool()
        print("\n" + "=" * 60)
        print("🎉 所有测试通过！")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
