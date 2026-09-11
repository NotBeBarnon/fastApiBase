# @Description : Typer 命令行入口
from __future__ import annotations

import asyncio

import typer

Application = typer.Typer()


@Application.command()
def run(
    host: str | None = None,
    port: int | None = None,
    reload: bool = False,
) -> None:
    import uvicorn

    from .faster import fast_app
    from .settings import HTTP_API_LISTEN_HOST, HTTP_API_LISTEN_PORT

    uvicorn.run(
        fast_app,
        host=host or HTTP_API_LISTEN_HOST,
        port=port or HTTP_API_LISTEN_PORT,
        reload=reload,
    )


@Application.command()
def mcp() -> None:
    """
    启动 MCP stdio 服务器。

    用于 Claude Desktop、Cline 等支持 MCP 的桌面 AI 客户端。
    在 MCP 客户端配置中添加此命令即可。
    """
    from .my_tools.mcp_tools.server import MCPServer, MCPStdioTransport
    from .version import VERSION

    server = MCPServer(name="fastapi-base-mcp", version=VERSION)
    transport = MCPStdioTransport(server=server)
    asyncio.run(transport.run())
