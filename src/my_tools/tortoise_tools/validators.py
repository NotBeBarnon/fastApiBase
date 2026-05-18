# -*- coding: utf-8 -*-
# @Description : Tortoise 字段验证器
from __future__ import annotations

from tortoise.exceptions import ValidationError
from tortoise.validators import Validator


class MaxValidator(Validator):
    """最大值验证器"""

    def __init__(self, num: int) -> None:
        self.num = num

    def __call__(self, value: int) -> None:
        if value > self.num:
            raise ValidationError(f"Value '{value}' exceeds the maximum {self.num}")


class MinValidator(Validator):
    """最小值验证器"""

    def __init__(self, num: int) -> None:
        self.num = num

    def __call__(self, value: int) -> None:
        if value < self.num:
            raise ValidationError(f"Value '{value}' is below the minimum {self.num}")


class NotValidator(Validator):
    """非值验证器"""

    def __init__(self, values: list[str | int]) -> None:
        self.values = values

    def __call__(self, value: str | int) -> None:
        if value in self.values:
            raise ValidationError(f"Value '{value}' cannot be in {self.values}")


class InValidator(Validator):
    """取值验证器"""

    def __init__(self, values: list[str | int]) -> None:
        self.values = values

    def __call__(self, value: str | int) -> None:
        if value not in self.values:
            raise ValidationError(f"Value '{value}' must be in {self.values}")
