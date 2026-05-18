# -*- coding: utf-8 -*-
# @Description : Kafka 消费回调基类
from __future__ import annotations

import abc

from aiokafka import ConsumerRecord

from src.my_tools.singleton_tools import SingletonABCMeta

__all__ = ("BaseTopicCall", "BaseTopicCallSingle")


class BaseTopicCall(metaclass=abc.ABCMeta):
    @property
    @abc.abstractmethod
    def topic(self) -> str: ...

    @abc.abstractmethod
    async def callback(self, msg: ConsumerRecord) -> None: ...


class BaseTopicCallSingle(BaseTopicCall, metaclass=SingletonABCMeta):
    @abc.abstractmethod
    async def callback(self, msg: ConsumerRecord) -> None: ...

    @property
    @abc.abstractmethod
    def topic(self) -> str: ...
