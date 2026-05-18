# -*- coding: utf-8 -*-
# @Description : emqx 连接（占位实现）
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

import gmqtt

client_id = f"MQTT_{os.urandom(8).hex()}"
gmqClient = gmqtt.Client(client_id)


class GMQTT:
    def __init__(
        self,
        topics: list[str],
        on_message: Callable,
        loop_: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.mqtt_ip = "127.0.0.1"
        self.mqtt_port = "1234"
        self.topics = topics
        self._loop = loop_ if loop_ else asyncio.get_event_loop()
        self.gmqClient = gmqClient
