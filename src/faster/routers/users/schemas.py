# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator
from tortoise.contrib.pydantic import pydantic_model_creator

from .models import User

UserSchema = pydantic_model_creator(User, name="UserSchema", exclude=("password",))


class UserCreateSchema(
    pydantic_model_creator(User, name="UserCreateSchema", exclude=("uid",), exclude_readonly=True)
):
    model_config = ConfigDict(title="UserCreateSchema")

    password_again: str = Field(..., description="重复输入验证密码")

    @field_validator("password_again")
    @classmethod
    def password_again_validator(cls, password_again: str, info) -> str:
        if password_again != info.data.get("password"):
            raise ValueError("两次密码不一致")
        return password_again


class UserUpdateSchema(
    pydantic_model_creator(User, name="UserUpdateSchema", exclude=("uid",), exclude_readonly=True)
):
    model_config = ConfigDict(title="UserUpdateSchema")

    username: str | None = None
    password: str | None = None
    password_again: str | None = Field(None, description="再次验证密码")

    @field_validator("password")
    @classmethod
    def password_validator(cls, password: str | None) -> str | None:
        if password is not None and not password:
            raise ValueError("密码不可设置为空")
        return password

    @field_validator("password_again")
    @classmethod
    def password_again_validator(cls, password_again: str | None, info) -> str | None:
        pwd = info.data.get("password")
        if pwd is not None and password_again != pwd:
            raise ValueError("密码不一致")
        return password_again
