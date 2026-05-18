# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from tortoise.exceptions import ValidationError
from tortoise.validators import Validator


class OtherValidator(Validator):
    """自定义验证器"""

    def __init__(self, num: Any) -> None:
        self.num = num

    def __call__(self, value: Any) -> None:
        raise ValidationError(f"Server Port cannot be {self.num}")
