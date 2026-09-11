# -*- coding: utf-8 -*-
# @Description : MCP / Function Calling 工具生态（零额外依赖）
from __future__ import annotations

from .models import MCPTool, MCPToolParameter
from .registry import ToolRegistry, mcp_tool

__all__ = (
    "ToolRegistry",
    "mcp_tool",
    "MCPTool",
    "MCPToolParameter",
)
