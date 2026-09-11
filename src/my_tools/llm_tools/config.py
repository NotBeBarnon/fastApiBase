# -*- coding: utf-8 -*-
# @Description : LLM 网关配置模型
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProviderType(str, Enum):
    """支持的模型厂商类型"""

    OPENAI = "openai"          # OpenAI 兼容（DeepSeek / 通义 / 豆包 / Kimi 等都兼容）
    ANTHROPIC = "anthropic"    # Anthropic Claude
    AZURE = "azure"            # Azure OpenAI
    LOCAL = "local"            # 本地模型（Ollama / vLLM / llama.cpp）
    CUSTOM = "custom"          # 自定义 OpenAI 兼容接口


class LLMProvider(BaseModel):
    """单个模型提供商配置"""

    name: str                                      # 标识名，如 "deepseek" / "gpt-4"
    type: ProviderType = ProviderType.OPENAI       # 厂商类型
    base_url: str = ""                             # API 地址
    api_key: str = ""                              # API 密钥
    default_model: str = ""                        # 默认模型名
    timeout: int = 60                              # 超时秒数
    max_retries: int = 2                           # 最大重试次数
    priority: int = 100                            # 优先级（数字越大越优先）
    enabled: bool = True                           # 是否启用
    extra: dict[str, Any] = Field(default_factory=dict)  # 额外参数


class LLMConfig(BaseModel):
    """LLM 网关总配置"""

    default_provider: str = ""                     # 默认使用的 provider name
    fallback_providers: list[str] = Field(default_factory=list)  # 降级顺序
    providers: dict[str, LLMProvider] = Field(default_factory=dict)
    global_timeout: int = 60
    enable_cost_tracking: bool = True              # 是否启用费用统计
