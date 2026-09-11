# -*- coding: utf-8 -*-
# @Description : FastAPI 鉴权依赖（API Key + JWT Bearer）
from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from loguru import logger

from src.settings import DEV, SECURITY_CONFIG

from .jwt_tools import TokenError, decode_token

__all__ = ("APIKeyInfo", "verify_api_key", "require_api_key", "verify_jwt")

_DEFAULT_JWT_SECRET = "fast-sample-secret-change-me"
if SECURITY_CONFIG["jwt_secret"] == _DEFAULT_JWT_SECRET and not DEV:
    logger.warning("Security: jwt_secret is the default value, change it via pyproject.toml [myproject.security]!")


@dataclass
class APIKeyInfo:
    name: str
    role: str


async def verify_api_key(request: Request) -> APIKeyInfo:
    """从 X-API-Key 头校验 API Key（secrets.compare_digest 防时序攻击），失败抛 401。"""
    if not SECURITY_CONFIG["auth_enabled"]:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth disabled")

    key = request.headers.get("x-api-key", "").strip()
    if not key:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    for name, info in SECURITY_CONFIG["api_keys"].items():
        if secrets.compare_digest(info["key"], key):
            return APIKeyInfo(name=name, role=info["role"])

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key", headers={"WWW-Authenticate": "ApiKey"})


def require_api_key(role: str | None = None):
    """API Key 依赖工厂；指定 role 时做角色校验，不匹配抛 403。"""

    async def dependency(request: Request) -> APIKeyInfo:
        info = await verify_api_key(request)
        if role is not None and info.role != role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{role}' required, got '{info.role}'")
        return info

    return dependency


async def verify_jwt(request: Request) -> dict:
    """从 Authorization: Bearer 头解析并验签 JWT，失败抛 401，成功返回 payload dict。"""
    if not SECURITY_CONFIG["auth_enabled"]:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth disabled")

    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth[7:].strip()
    try:
        return decode_token(
            token,
            SECURITY_CONFIG["jwt_secret"],
            algorithms=(SECURITY_CONFIG["jwt_algorithm"],),
        )
    except TokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
