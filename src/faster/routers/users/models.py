# -*- coding: utf-8 -*-
from __future__ import annotations

from tortoise import fields, models

from src.my_tools.regex_tool import chinese_regex

from . import app_name


class User(models.Model):
    """用户"""

    user_number = fields.IntField(pk=True, description="用户编号")
    uid = fields.CharField(max_length=10, unique=True, description="用户UID，唯一标识用户")
    username = fields.CharField(max_length=32, description="用户名")
    name = fields.CharField(max_length=32, null=True, description="用户的名字")
    family_name = fields.CharField(max_length=32, null=True, description="用户的姓氏")
    password = fields.CharField(max_length=64, description="密码")
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")
    modified_at = fields.DatetimeField(auto_now=True, description="修改时间")

    def full_name(self) -> str:
        if self.name or self.family_name:
            if chinese_regex.search(f"{self.name}") or chinese_regex.search(f"{self.family_name}"):
                return f"{self.family_name or ''}{self.name or ''}".strip()
            return f"{self.name or ''} {self.family_name or ''}".strip()
        return self.username

    class PydanticMeta:
        computed = ("full_name",)
        exclude = ("user_number", "created_at", "modified_at")

    class Meta:
        table = f"{app_name}_user"
