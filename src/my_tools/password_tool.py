# -*- coding: utf-8 -*-
# @Description : 密码工具（MD5，仅作示例 — 生产环境建议使用 argon2/bcrypt）
from __future__ import annotations

import hashlib


def make_password(password: str) -> str:
    return hashlib.md5(password.encode("ascii")).hexdigest()
