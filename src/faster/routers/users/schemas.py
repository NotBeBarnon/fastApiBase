from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, description="用户名（字母/数字/下划线）")
    password: str = Field(..., min_length=8, max_length=64, description="密码（至少 8 位）")
    password_again: str = Field(..., description="重复密码")

    @field_validator("username")
    @classmethod
    def username_validator(cls, username: str) -> str:
        if not username.replace("_", "").isalnum():
            raise ValueError("用户名仅允许字母、数字、下划线")
        return username

    @field_validator("password_again")
    @classmethod
    def password_again_validator(cls, password_again: str, info) -> str:
        if password_again != info.data.get("password"):
            raise ValueError("两次输入的密码不一致")
        return password_again


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=1, max_length=64)


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=64, description="旧密码")
    new_password: str = Field(..., min_length=8, max_length=64, description="新密码（至少 8 位）")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime | None = None
