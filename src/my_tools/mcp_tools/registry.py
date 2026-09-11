# -*- coding: utf-8 -*-
# @Description : 工具注册表 + @mcp_tool 装饰器（零依赖，纯 Python）
from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Callable
from typing import Any

from loguru import logger

from .models import MCPTool, MCPToolParameter

__all__ = ("ToolRegistry", "mcp_tool")


# ---------------------------------------------------------------------------
# @mcp_tool 装饰器
# ---------------------------------------------------------------------------
def mcp_tool(
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
):
    """
    将一个函数/方法标记为 MCP / Function Calling 工具。

    用法：
        @mcp_tool(description="获取用户信息")
        async def get_user(user_id: int) -> dict: ...

    参数说明会自动从 docstring 中解析（支持 Google / NumPy / reST 风格）。
    类型注解用于推断 JSON Schema 类型。
    """

    def decorator(func: Callable) -> Callable:
        # 提取元数据
        tool_name = name or func.__name__
        doc = description or func.__doc__ or ""
        doc_description, param_docs = _parse_docstring(doc)

        # 从函数签名提取参数
        sig = inspect.signature(func)
        parameters: list[MCPToolParameter] = []
        for param_name, param in sig.parameters.items():
            # 跳过 self / cls
            if param_name in ("self", "cls"):
                continue
            # 推断类型
            param_type = _py_type_to_json_type(param.annotation)
            # 是否必填
            required = param.default is inspect.Parameter.empty
            default = None if required else param.default
            # 参数描述
            param_desc = param_docs.get(param_name, "")
            parameters.append(
                MCPToolParameter(
                    name=param_name,
                    type=param_type,
                    description=param_desc,
                    required=required,
                    default=default,
                )
            )

        tool = MCPTool(
            name=tool_name,
            description=doc_description.strip(),
            parameters=parameters,
            handler=func,
            tags=tags or [],
        )

        # 挂到函数对象上，供 ToolRegistry 扫描
        func.__mcp_tool__ = tool
        return func

    return decorator


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------
class ToolRegistry:
    """
    全局工具注册表。

    - 支持手动注册：register(func) / register_tool(tool)
    - 支持从 ViewSet 类自动扫描：scan_viewset(viewset_cls)
    - 支持按 tag 过滤
    - 导出为 OpenAI function list 或 MCP tool list
    - 调用工具：call(name, kwargs)
    """

    _instance: "ToolRegistry | None" = None

    def __init__(self) -> None:
        self._tools: dict[str, MCPTool] = {}

    @classmethod
    def get_instance(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # —— 注册 ——
    def register(self, func: Callable) -> MCPTool | None:
        """注册一个带 @mcp_tool 装饰器的函数"""
        tool = getattr(func, "__mcp_tool__", None)
        if tool is None:
            logger.warning(f"Function '{func.__name__}' is not decorated with @mcp_tool")
            return None
        return self.register_tool(tool)

    def register_tool(self, tool: MCPTool) -> MCPTool:
        """注册一个 MCPTool 实例"""
        if tool.name in self._tools:
            logger.warning(f"Tool '{tool.name}' already registered, overwriting")
        self._tools[tool.name] = tool
        logger.debug(f"Registered MCP tool: {tool.name}")
        return tool

    def scan_viewset(self, viewset_cls: type) -> list[MCPTool]:
        """
        从一个 ViewSet 类中扫描所有带 @mcp_tool 的方法并注册。

        同时，对于未装饰但符合命名约定的 CRUD 方法（get/create/update/delete/all），
        如果有 __fast_view__ 标记，也会自动转换为 MCP 工具（只读操作默认开启，写操作需要显式装饰）。
        """
        registered: list[MCPTool] = []
        viewset_name = viewset_cls.__name__

        for attr_name in dir(viewset_cls):
            if attr_name.startswith("_"):
                continue
            attr = getattr(viewset_cls, attr_name)
            if not callable(attr):
                continue

            # 已装饰的直接注册
            if hasattr(attr, "__mcp_tool__"):
                tool = self._wrap_viewset_tool(viewset_cls, attr.__mcp_tool__)
                registered.append(tool)
                continue

            # 自动转换：只读操作（all / get）
            if hasattr(attr, "__fast_view__") and attr_name in ("all", "get"):
                fast_view = attr.__fast_view__
                tool = MCPTool(
                    name=f"{viewset_name.lower()}_{attr_name}",
                    description=fast_view.get("summary") or f"{viewset_name} {attr_name}",
                    parameters=_infer_params_from_viewset(attr, attr_name),
                    handler=None,  # 后面通过 viewset 实例调用
                    tags=["viewset", "read"],
                )
                self._tools[tool.name] = tool
                registered.append(tool)

        logger.info(f"Scanned {len(registered)} tools from ViewSet {viewset_name}")
        return registered

    def _wrap_viewset_tool(self, viewset_cls: type, tool: MCPTool) -> MCPTool:
        """包装 ViewSet 的方法，使其可以被独立调用"""
        original_handler = tool.handler

        async def _handler(**kwargs):
            instance = viewset_cls()
            if asyncio.iscoroutinefunction(original_handler):
                return await original_handler(instance, **kwargs)
            return original_handler(instance, **kwargs)

        new_tool = MCPTool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            handler=_handler,
            tags=tool.tags,
        )
        self._tools[new_tool.name] = new_tool
        return new_tool

    # —— 查询 ——
    def get_tool(self, name: str) -> MCPTool | None:
        return self._tools.get(name)

    def list_tools(self, tags: list[str] | None = None) -> list[MCPTool]:
        """列出所有工具，可按 tag 过滤"""
        tools = list(self._tools.values())
        if tags:
            tools = [t for t in tools if any(tag in t.tags for tag in tags)]
        return tools

    # —— 导出 ——
    def to_openai_functions(self, tags: list[str] | None = None) -> list[dict]:
        """导出为 OpenAI Function Calling 格式"""
        return [t.to_openai_function() for t in self.list_tools(tags)]

    def to_mcp_tools(self, tags: list[str] | None = None) -> list[dict]:
        """导出为 MCP tools/list 格式"""
        return [t.to_mcp_tool() for t in self.list_tools(tags)]

    # —— 调用 ——
    async def call_tool(self, name: str, arguments: dict | str | None = None) -> dict:
        """
        调用一个工具。

        arguments 可以是 dict 或 JSON 字符串。
        返回 {"content": [{"type": "text", "text": "..."}]} 格式（MCP 兼容）。
        """
        tool = self._tools.get(name)
        if tool is None:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Tool not found: {name}"}],
            }

        # 解析参数
        kwargs: dict = {}
        if arguments:
            if isinstance(arguments, str):
                try:
                    kwargs = json.loads(arguments)
                except json.JSONDecodeError as e:
                    return {
                        "isError": True,
                        "content": [{"type": "text", "text": f"Invalid JSON arguments: {e}"}],
                    }
            elif isinstance(arguments, dict):
                kwargs = arguments

        # 校验必填参数
        missing = [p.name for p in tool.parameters if p.required and p.name not in kwargs]
        if missing:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Missing required parameters: {missing}"}],
            }

        # 调用
        try:
            handler = tool.handler
            if handler is None:
                return {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Tool '{name}' has no handler"}],
                }
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**kwargs)
            else:
                result = handler(**kwargs)

            # 格式化结果
            text = _result_to_text(result)
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:
            logger.exception(f"Tool '{name}' execution error")
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Tool error: {exc.__class__.__name__}: {exc}"}],
            }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _py_type_to_json_type(annotation: Any) -> str:
    """将 Python 类型注解转换为 JSON Schema 类型"""
    if annotation is inspect.Parameter.empty:
        return "string"
    # 处理 Optional[X] / X | None
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())
    if origin is not None and type(None) in args:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _py_type_to_json_type(non_none[0])
    # 基本类型映射
    type_map = {
        int: "integer",
        float: "number",
        str: "string",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    return type_map.get(annotation, "string")


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """
    简单的 docstring 解析器，提取描述和参数说明。
    支持 Args: / Parameters: / :param 风格。
    """
    if not doc:
        return "", {}

    lines = doc.strip().split("\n")
    description_lines: list[str] = []
    param_docs: dict[str, str] = {}
    in_params = False
    current_param: str | None = None
    current_desc_lines: list[str] = []

    param_pattern = re.compile(r"^\s*(?:Args:|Parameters:|params?:?)\s*$", re.IGNORECASE)
    param_line_pattern = re.compile(r"^\s*(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$")
    rest_param_pattern = re.compile(r"^\s*:param\s+(\w+)\s*:\s*(.*)$")

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if in_params and current_param and current_desc_lines:
                param_docs[current_param] = " ".join(current_desc_lines).strip()
                current_param = None
                current_desc_lines = []
            continue

        # 检测参数段开始
        if param_pattern.match(stripped):
            in_params = True
            continue

        # reST 风格 :param name: desc
        rest_match = rest_param_pattern.match(stripped)
        if rest_match:
            in_params = True
            param_docs[rest_match.group(1)] = rest_match.group(2).strip()
            continue

        if in_params:
            match = param_line_pattern.match(line)
            if match:
                if current_param and current_desc_lines:
                    param_docs[current_param] = " ".join(current_desc_lines).strip()
                current_param = match.group(1)
                current_desc_lines = [match.group(2)] if match.group(2) else []
            elif current_param:
                current_desc_lines.append(stripped)
        else:
            description_lines.append(stripped)

    if current_param and current_desc_lines:
        param_docs[current_param] = " ".join(current_desc_lines).strip()

    description = " ".join(description_lines).strip()
    return description, param_docs


def _result_to_text(result: Any) -> str:
    """将工具调用结果格式化为文本"""
    if result is None:
        return "OK"
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, default=str)
    if isinstance(result, str):
        return result
    return str(result)


def _infer_params_from_viewset(func: Callable, method_name: str) -> list[MCPToolParameter]:
    """从 ViewSet 方法推断参数（简化版）"""
    params: list[MCPToolParameter] = []
    sig = inspect.signature(func)
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls", "request", "response"):
            continue
        params.append(
            MCPToolParameter(
                name=param_name,
                type=_py_type_to_json_type(param.annotation),
                description=f"{method_name} parameter",
                required=param.default is inspect.Parameter.empty,
            )
        )
    return params
