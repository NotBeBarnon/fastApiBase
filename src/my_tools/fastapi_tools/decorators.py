# -*- coding: utf-8 -*-
# @Description : FastAPI Action 装饰器（HTTP 方法路由元数据载体）
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any

from fastapi import params
from fastapi.datastructures import Default
from fastapi.responses import ORJSONResponse, Response
from fastapi.types import DecoratedCallable
from starlette.routing import BaseRoute
from starlette.routing import Mount as Mount  # noqa: F401

DictIntStrAny = dict[int | str, Any]
SetIntStr = set[int | str]


class Action:
    """将 HTTP 路由元数据附着到函数对象上，供 BaseViewSet 注册时读取"""

    def __init__(
        self,
        path: str,
        *,
        methods: list[str] | None = None,
        response_model: type[Any] | None = None,
        status_code: int | None = None,
        tags: list[str] | None = None,
        dependencies: Sequence[params.Depends] | None = None,
        summary: str | None = None,
        description: str | None = None,
        response_description: str = "Successful Response",
        responses: dict[int | str, dict[str, Any]] | None = None,
        deprecated: bool | None = None,
        operation_id: str | None = None,
        response_model_include: SetIntStr | DictIntStrAny | None = None,
        response_model_exclude: SetIntStr | DictIntStrAny | None = None,
        response_model_by_alias: bool = True,
        response_model_exclude_unset: bool = False,
        response_model_exclude_defaults: bool = False,
        response_model_exclude_none: bool = False,
        include_in_schema: bool = True,
        response_class: type[Response] = Default(ORJSONResponse),
        name: str | None = None,
        callbacks: list[BaseRoute] | None = None,
        openapi_extra: dict[str, Any] | None = None,
    ) -> None:
        self.__fast_params = {
            "path": path,
            "response_model": response_model,
            "status_code": status_code,
            "tags": tags,
            "dependencies": dependencies,
            "summary": summary,
            "description": description,
            "response_description": response_description,
            "responses": responses,
            "deprecated": deprecated,
            "methods": methods,
            "operation_id": operation_id,
            "response_model_include": response_model_include,
            "response_model_exclude": response_model_exclude,
            "response_model_by_alias": response_model_by_alias,
            "response_model_exclude_unset": response_model_exclude_unset,
            "response_model_exclude_defaults": response_model_exclude_defaults,
            "response_model_exclude_none": response_model_exclude_none,
            "include_in_schema": include_in_schema,
            "response_class": response_class,
            "name": name,
            "callbacks": callbacks,
            "openapi_extra": openapi_extra,
        }

    def __call__(self, func: Callable) -> DecoratedCallable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)

        wrapper.__dict__["__fast_view__"] = self.__fast_params
        return wrapper

    # —— 静态方法工厂 ——
    @staticmethod
    def _make(method: str, path: str, **kw) -> "Action":
        return Action(path, methods=[method], **kw)

    @staticmethod
    def get(path: str, **kw) -> "Action":
        return Action._make("GET", path, **kw)

    @staticmethod
    def post(path: str, **kw) -> "Action":
        return Action._make("POST", path, **kw)

    @staticmethod
    def put(path: str, **kw) -> "Action":
        return Action._make("PUT", path, **kw)

    @staticmethod
    def patch(path: str, **kw) -> "Action":
        return Action._make("PATCH", path, **kw)

    @staticmethod
    def delete(path: str, **kw) -> "Action":
        return Action._make("DELETE", path, **kw)
