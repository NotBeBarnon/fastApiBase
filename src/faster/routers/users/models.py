from __future__ import annotations

from tortoise import fields, models

from . import app_name


class User(models.Model):
    """用户（密码使用 PBKDF2-SHA256 带盐哈希存储）"""

    id = fields.IntField(pk=True, description="用户 ID")
    username = fields.CharField(max_length=32, unique=True, description="用户名（登录标识）")
    password_hash = fields.CharField(max_length=256, description="密码哈希")
    role = fields.CharField(max_length=16, default="user", description="角色：user / admin")
    is_active = fields.BooleanField(default=True, description="是否启用")
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")
    modified_at = fields.DatetimeField(auto_now=True, description="修改时间")

    class Meta:
        table = f"{app_name}_user"
        table_description = "用户表"
