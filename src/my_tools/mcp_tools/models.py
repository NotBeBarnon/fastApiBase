# -*- coding: utf-8 -*-
# @Description : MCP 工具数据模型（Pydantic v2）
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MCPToolParameter(BaseModel):
    """工具参数定义"""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: list[str] | None = None
    items: dict | None = None  # 用于 array 类型


class MCPTool(BaseModel):
    """工具定义（同时兼容 OpenAI Function Calling 与 MCP 协议）"""

    name: str
    description: str = ""
    parameters: list[MCPToolParameter] = Field(default_factory=list)
    # 内部调用 handler（不导出到协议层）
    handler: Any = Field(default=None, exclude=True)
    # 标签：用于分类过滤
    tags: list[str] = Field(default_factory=list)

    # —— 导出为 OpenAI Function Calling 格式 ——
    def to_openai_function(self) -> dict:
        """导出为 OpenAI function calling 的 JSON Schema 格式"""
        properties: dict[str, dict] = {}
        required: list[str] = []

        for param in self.parameters:
            prop: dict = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum is not None:
                prop["enum"] = param.enum
            if param.type == "array" and param.items is not None:
                prop["items"] = param.items
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    # —— 导出为 MCP 协议格式 ——
    def to_mcp_tool(self) -> dict:
        """导出为 MCP tools/list 返回的格式"""
        input_schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        for param in self.parameters:
            prop: dict = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum is not None:
                prop["enum"] = param.enum
            if param.type == "array" and param.items is not None:
                prop["items"] = param.items
            input_schema["properties"][param.name] = prop
            if param.required:
                input_schema["required"].append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": input_schema,
        }
