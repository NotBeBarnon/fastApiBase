# @Description : 资源模块模型（Beam 示例，带 owner 归属用户）
from __future__ import annotations

from enum import IntEnum

from tortoise import fields, models

from . import app_name


class BeamTypeEnum(IntEnum):
    """波束类型"""

    ka = 1
    x = 2


class Beam(models.Model):
    """波束资源（归属到具体用户，admin 可见全部）"""

    id = fields.IntField(pk=True, description="波束 ID")
    name = fields.CharField(max_length=50, description="波束名称")
    type = fields.IntEnumField(BeamTypeEnum, description="波束类型：1=KA, 2=X")
    owner = fields.ForeignKeyField(
        "users.User",
        related_name="beams",
        on_delete=fields.OnDelete.CASCADE,
        description="所属用户",
    )
    is_active = fields.BooleanField(default=True, description="是否启用")
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")
    modified_at = fields.DatetimeField(auto_now=True, description="修改时间")

    class Meta:
        table = f"{app_name}_beam"
        table_description = "波束资源表"
        indexes = [("owner_id", "type"), ("name",)]
