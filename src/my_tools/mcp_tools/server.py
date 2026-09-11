# @Description : MCP 协议服务器（零依赖，支持 SSE 和 stdio 两种传输）
# 协议参考：https://modelcontextprotocol.io/specification
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from loguru import logger

from .registry import ToolRegistry

__all__ = (
    "MCPServer",
    "MCPSSETransport",
    "MCPStdioTransport",
)


# ---------------------------------------------------------------------------
# MCP 服务器核心（协议处理逻辑）
# ---------------------------------------------------------------------------
class MCPServer:
    """
    MCP 协议服务器核心。

    负责处理 JSON-RPC 请求、调度工具调用、生成响应。
    与传输层解耦，可挂在 SSE、stdio、WebSocket 等任意传输上。
    """

    def __init__(self, name: str = "fastapi-mcp", version: str = "1.0.0") -> None:
        self.name = name
        self.version = version
        self.registry = ToolRegistry.get_instance()
        self._initialized = False

    async def handle_request(self, request: dict) -> dict | None:
        """处理一条 JSON-RPC 请求，返回响应（notification 返回 None）"""
        method = request.get("method", "")
        request_id = request.get("id")
        params = request.get("params", {})

        # MCP 核心方法
        handlers = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "ping": self._handle_ping,
        }

        handler = handlers.get(method)
        if handler is None:
            if request_id is not None:
                return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")
            return None

        try:
            result = await handler(params)
            if request_id is not None:
                return _jsonrpc_response(request_id, result)
            return None
        except Exception as exc:
            logger.exception(f"MCP handler error: {method}")
            if request_id is not None:
                return _jsonrpc_error(request_id, -32603, f"Internal error: {exc}")
            return None

    async def _handle_initialize(self, params: dict) -> dict:
        self._initialized = True
        logger.info(f"MCP initialized by {params.get('clientInfo', {})}")
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "logging": {},
            },
            "serverInfo": {
                "name": self.name,
                "version": self.version,
            },
        }

    async def _handle_tools_list(self, params: dict) -> dict:
        tools = self.registry.to_mcp_tools()
        return {"tools": tools}

    async def _handle_tools_call(self, params: dict) -> dict:
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        return await self.registry.call_tool(name, arguments)

    async def _handle_ping(self, params: dict) -> dict:
        return {}


# ---------------------------------------------------------------------------
# SSE 传输（用于 FastAPI 路由）
# ---------------------------------------------------------------------------
class MCPSSETransport:
    """
    MCP SSE 传输层。

    客户端通过 GET /mcp/sse 建立 SSE 连接，
    然后通过 POST /mcp/messages 发送 JSON-RPC 请求。

    完全符合 MCP SSE 传输规范。
    """

    def __init__(self, server: MCPServer | None = None) -> None:
        self.server = server or MCPServer()
        # session_id -> queue
        self._sessions: dict[str, asyncio.Queue] = {}

    def create_session(self) -> str:
        """创建一个新的 SSE 会话，返回 session_id"""
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = asyncio.Queue()
        logger.debug(f"MCP SSE session created: {session_id}")
        return session_id

    async def sse_stream(self, session_id: str) -> AsyncGenerator[str, None]:
        """
        SSE 事件流生成器。

        首先发送 endpoint 事件告知客户端消息提交地址，
        然后持续推送 response / error 事件。
        """
        queue = self._sessions.get(session_id)
        if queue is None:
            return

        # 第一条消息：endpoint 事件（告知客户端 POST 地址）
        yield f"event: endpoint\ndata: /mcp/messages?session_id={session_id}\n\n"

        try:
            while True:
                message = await queue.get()
                if message is None:  # 结束信号
                    break
                yield f"data: {json.dumps(message, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            logger.debug(f"MCP SSE session cancelled: {session_id}")
        finally:
            self._sessions.pop(session_id, None)
            logger.debug(f"MCP SSE session closed: {session_id}")

    async def post_message(self, session_id: str, body: Any) -> dict | None:
        """
        处理客户端 POST 过来的一条 JSON-RPC 消息，
        将响应放入对应 session 的 SSE 队列。
        """
        queue = self._sessions.get(session_id)
        if queue is None:
            return {"error": f"Session not found: {session_id}"}

        # 解析请求
        if isinstance(body, str):
            try:
                request = json.loads(body)
            except json.JSONDecodeError:
                return {"error": "Invalid JSON"}
        else:
            request = body

        response = await self.server.handle_request(request)
        if response is not None:
            await queue.put(response)

        return {"status": "ok"}

    def close_session(self, session_id: str) -> None:
        queue = self._sessions.get(session_id)
        if queue is not None:
            queue.put_nowait(None)


# ---------------------------------------------------------------------------
# stdio 传输（用于 CLI 命令行）
# ---------------------------------------------------------------------------
class MCPStdioTransport:
    """
    MCP stdio 传输层。

    从 stdin 读取 JSON-RPC 请求，处理后写到 stdout。
    每行一条 JSON（JSON-RPC 2.0 over line-delimited JSON）。
    用于 Claude Desktop、Cline 等桌面 AI 工具。
    """

    def __init__(self, server: MCPServer | None = None) -> None:
        self.server = server or MCPServer()
        self._running = False

    async def run(self) -> None:
        """启动 stdio 循环（阻塞，直到收到 exit 或 EOF）"""
        self._running = True
        logger.info("MCP stdio server started")

        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # 用 run_in_executor 避免阻塞事件循环
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from stdin: {line[:100]}")
                    continue

                response = await self.server.handle_request(request)
                if response is not None:
                    print(json.dumps(response, ensure_ascii=False), flush=True)

            except Exception as exc:
                logger.exception(f"MCP stdio error: {exc}")

        logger.info("MCP stdio server stopped")

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# JSON-RPC 辅助函数
# ---------------------------------------------------------------------------
def _jsonrpc_response(id: Any, result: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": id,
        "result": result,
    }


def _jsonrpc_error(id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": code,
            "message": message,
        },
    }
