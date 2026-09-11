# @Description : Kafka 消费回调基类
from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from src.my_tools.singleton_tools import SingletonABCMeta

if TYPE_CHECKING:  # 避免未安装 aiokafka 的环境（如未启用 MQ）导入失败
    from aiokafka import ConsumerRecord

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
