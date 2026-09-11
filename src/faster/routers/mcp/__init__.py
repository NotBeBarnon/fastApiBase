# -*- coding: utf-8 -*-
# @Description : MCP Server 路由（SSE 传输 + 消息提交）
from __future__ import annotations

from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse, StreamingResponse

from ...my_tools.mcp_tools import ToolRegistry
from ...my_tools.mcp_tools.server import MCPSSETransport, MCPServer
from ...version import VERSION

__all__ = ("mcp_router",)

mcp_router = APIRouter(prefix="/mcp", tags=["mcp"])

# 全局 MCP SSE 传输实例
_mcp_server = MCPServer(name="fastapi-base-mcp", version=VERSION)
_sse_transport = MCPSSETransport(server=_mcp_server)


@mcp_router.get("/sse")
async def mcp_sse() -> StreamingResponse:
    """
    MCP SSE 连接端点。

    客户端通过 SSE 接收服务端推送的响应消息，
    再通过 POST /mcp/messages 发送请求。
    """
    session_id = _sse_transport.create_session()
    return StreamingResponse(
        _sse_transport.sse_stream(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "MCP-Session-Id": session_id,
        },
    )


@mcp_router.post("/messages")
async def mcp_messages(
    request: Request,
    session_id: str = Query(..., description="SSE 会话 ID"),
) -> JSONResponse:
    """
    MCP 消息提交端点。

    客户端将 JSON-RPC 请求通过 POST 发送到这里，
    响应会通过对应的 SSE 会话推送。
    """
    body = await request.json()
    result = await _sse_transport.post_message(session_id, body)
    if result and "error" in result:
        return JSONResponse(status_code=400, content=result)
    return JSONResponse(content={"status": "accepted"})


@mcp_router.get("/tools")
async def mcp_tools_list() -> JSONResponse:
    """
    直接获取所有 MCP 工具列表（REST 风格，便于调试）。
    """
    tools = ToolRegistry.get_instance().to_mcp_tools()
    return JSONResponse(content={"tools": tools})


@mcp_router.get("/tools/openai")
async def openai_functions_list() -> JSONResponse:
    """
    获取 OpenAI Function Calling 格式的工具列表。
    """
    functions = ToolRegistry.get_instance().to_openai_functions()
    return JSONResponse(content={"functions": functions})
