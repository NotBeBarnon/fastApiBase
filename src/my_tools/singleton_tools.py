# -*- coding: utf-8 -*-
# @Description : 单例工具
from __future__ import annotations

import abc
from typing import Any


class SingletonDecorator:
    """单例装饰器（按调用一次即创建一个实例）"""

    def __init__(self, cls: type) -> None:
        self._cls = cls
        self._instance: dict[type, Any] = {}

    def __call__(self) -> Any:
        if self._cls not in self._instance:
            self._instance[self._cls] = self._cls()
        return self._instance[self._cls]


class SingletonMeta(type):
    __instances: dict[type, Any] = {}

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        if cls not in cls.__instances:
            cls.__instances[cls] = super().__call__(*args, **kwargs)
        return cls.__instances[cls]


class SingletonABCMeta(abc.ABCMeta, SingletonMeta):
    pass
