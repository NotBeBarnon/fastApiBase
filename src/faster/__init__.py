# -*- coding: utf-8 -*-
# @Description : FastAPI 应用层（fast_app 惰性导出，避免导入子模块时拉起 events/apscheduler 等重依赖）
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .apps import fast_app

__all__ = ("fast_app",)


def __getattr__(name: str):
    if name == "fast_app":
        from .apps import fast_app

        return fast_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
