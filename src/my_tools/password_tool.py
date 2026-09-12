# @Description : 密码工具（PBKDF2-SHA256 + 随机盐，stdlib 零依赖）
from __future__ import annotations

import hashlib
import hmac
import secrets

__all__ = ("hash_password", "verify_password")

_ITERATIONS = 100_000
_SALT_BYTES = 16
_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    """生成带盐哈希，存储格式：pbkdf2_sha256$iterations$salt_hex$hash_hex"""
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
    return f"{_ALGORITHM}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验密码；存储格式非法直接返回 False"""
    try:
        algorithm, iterations, salt, expected_hex = stored.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations))
        return hmac.compare_digest(digest.hex(), expected_hex)
    except (ValueError, AttributeError):
        return False
