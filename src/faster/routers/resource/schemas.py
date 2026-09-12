# @Description : 资源模块 Schemas（Pydantic v2 原生）
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import BeamTypeEnum


class BeamBase(BaseModel):
    """Beam 基础字段"""

    name: str = Field(..., min_length=1, max_length=50, description="波束名称")
    type: BeamTypeEnum = Field(..., description="波束类型：ka=1, x=2")


class BeamCreate(BeamBase):
    """创建请求"""

    is_active: bool = Field(True, description="是否启用")


class BeamUpdate(BaseModel):
    """更新请求（部分更新，所有字段可选）"""

    name: str | None = Field(None, min_length=1, max_length=50, description="波束名称")
    type: BeamTypeEnum | None = Field(None, description="波束类型：ka=1, x=2")
    is_active: bool | None = Field(None, description="是否启用")


class BeamOut(BeamBase):
    """对外输出（不含敏感字段）"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    owner_id: int
    created_at: datetime
    modified_at: datetime


__all__ = ("BeamTypeEnum", "BeamCreate", "BeamUpdate", "BeamOut")
