# @Description : MCP 工具示例（演示 @mcp_tool 装饰器的用法）
from __future__ import annotations

import random
from datetime import datetime

from ..mcp_tools import mcp_tool


@mcp_tool(description="获取当前服务器时间", tags=["utility", "read"])
async def get_current_time(timezone: str = "Asia/Shanghai") -> dict:
    """
    获取当前服务器的日期时间。

    Args:
        timezone: 时区名称，如 Asia/Shanghai、UTC、America/New_York

    Returns:
        当前时间的字典，包含 iso 格式和可读格式
    """
    now = datetime.now()
    return {
        "iso": now.isoformat(),
        "readable": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": timezone,
    }


@mcp_tool(description="生成指定范围内的随机整数", tags=["utility", "read"])
async def random_number(min_value: int = 0, max_value: int = 100) -> dict:
    """
    生成一个 [min_value, max_value] 范围内的随机整数。

    Args:
        min_value: 最小值（包含）
        max_value: 最大值（包含）

    Returns:
        生成的随机数
    """
    if min_value > max_value:
        min_value, max_value = max_value, min_value
    return {
        "random": random.randint(min_value, max_value),
        "range": [min_value, max_value],
    }


@mcp_tool(description="回显输入的消息（用于测试连通性）", tags=["utility", "read"])
async def echo(message: str) -> str:
    """
    将输入的消息原样返回。

    Args:
        message: 要回显的消息

    Returns:
        原始消息
    """
    return message


# 注册到全局 ToolRegistry
def register_example_tools() -> None:
    """将示例工具注册到全局 ToolRegistry"""
    from ..mcp_tools import ToolRegistry

    registry = ToolRegistry.get_instance()
    registry.register(get_current_time)
    registry.register(random_number)
    registry.register(echo)
