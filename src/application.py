# -*- coding: utf-8 -*-
# @Description : Typer 命令行入口
from __future__ import annotations

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
