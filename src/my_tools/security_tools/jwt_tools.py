# -*- coding: utf-8 -*-
# @Description : 零依赖 JWT 编解码（stdlib hmac/base64/json 实现 HS256/HS384/HS512）
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

__all__ = ("TokenError", "create_token", "decode_token")


class TokenError(ValueError):
    """令牌无效（格式/签名/过期）"""


_HMAC_DIGESTS = {
    "HS256": hashlib.sha256,
    "HS384": hashlib.sha384,
    "HS512": hashlib.sha512,
}


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _sign(signing_input: str, secret: str, algorithm: str) -> str:
    digest = _HMAC_DIGESTS.get(algorithm)
    if digest is None:
        raise TokenError(f"Unsupported algorithm: {algorithm}")
    mac = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), digest)
    return _b64url_encode(mac.digest())


def create_token(
    payload: dict,
    secret: str,
    *,
    algorithm: str = "HS256",
    expires_seconds: int = 3600,
    extra_headers: dict | None = None,
) -> str:
    """
    签发 JWT。自动附加 iat / exp / jti 标准声明，payload 同名字段以传入值为准。
    """
    now = int(time.time())
    body = {
        "iat": now,
        "exp": now + int(expires_seconds),
        "jti": uuid.uuid4().hex,
        **payload,
    }
    header = {"alg": algorithm, "typ": "JWT", **(extra_headers or {})}
    signing_input = ".".join(
        (
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(body, separators=(",", ":")).encode("utf-8")),
        )
    )
    return f"{signing_input}.{_sign(signing_input, secret, algorithm)}"


def decode_token(token: str, secret: str, *, algorithms: tuple[str, ...] = ("HS256",)) -> dict:
    """
    验签并解码 JWT，校验 alg 白名单与 exp 过期时间，返回 payload dict。
    任何异常统一抛 TokenError。
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("Invalid token format")

    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, TypeError) as exc:
        raise TokenError(f"Invalid token encoding: {exc}") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise TokenError("Invalid token structure")

    algorithm = header.get("alg", "")
    if algorithm not in algorithms or algorithm not in _HMAC_DIGESTS:
        raise TokenError(f"Algorithm not allowed: {algorithm}")

    expected = _sign(f"{header_b64}.{payload_b64}", secret, algorithm)
    if not hmac.compare_digest(expected, signature_b64):
        raise TokenError("Signature verification failed")

    exp = payload.get("exp")
    if exp is not None:
        try:
            exp_value = float(exp)
        except (TypeError, ValueError) as exc:
            raise TokenError(f"Invalid exp claim: {exp}") from exc
        if exp_value < time.time():
            raise TokenError("Token expired")
    return payload
