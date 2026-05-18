# -*- coding: utf-8 -*-
# @Description : Tortoise 自定义字段
from __future__ import annotations

from random import choice
from typing import TYPE_CHECKING, Any

import arrow
from tortoise import ConfigurationError, ModelMeta
from tortoise.fields import Field
from tortoise.validators import MaxLengthValidator

if TYPE_CHECKING:
    from tortoise.models import Model

NUMBER_SEQUENCE = "0123456789"


def build_short_uid_pre() -> str:
    return arrow.now().format("YYMM")


def build_short_uid(*args: Any, **kwargs: Any) -> str:
    """生成短 uid"""
    suf = "".join(choice(NUMBER_SEQUENCE) for _ in range(5))
    return f"{build_short_uid_pre()}{suf}"


class ShortUIDField(Field, str):
    """自定义的短 uuid 字段"""

    def __init__(self, max_length: int, **kwargs: Any) -> None:
        if int(max_length) < 1:
            raise ConfigurationError("'max_length' must be >= 1")
        self.PRE_COUNT = {"pre": "0000", "count": 1}
        self.max_length = int(max_length)
        super().__init__(**kwargs)
        self.validators.append(MaxLengthValidator(self.max_length))

    def to_db_value(self, value: Any, instance: type[Model] | Model) -> str | None:
        if isinstance(instance, ModelMeta):
            return value and str(value)

        if not value:
            pre = build_short_uid_pre()
            if self.PRE_COUNT["pre"] == pre:
                suf = self.PRE_COUNT["count"] + 1
            else:
                self.PRE_COUNT = {"pre": "0000", "count": 1}
                suf = 1
            value = f"{pre}{suf:05d}"
        return value and str(value)

    @property
    def constraints(self) -> dict:
        return {"max_length": self.max_length}

    @property
    def SQL_TYPE(self) -> str:
        return f"CHAR({self.max_length})"
