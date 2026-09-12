# @Description : 用户路由（注册 / 登录 / 个人信息 / 管理员列表 / 改密码）
from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from tortoise.exceptions import ConfigurationError, DoesNotExist, IntegrityError, OperationalError

from src.my_tools.password_tool import hash_password, verify_password
from src.my_tools.security_tools import verify_jwt
from src.my_tools.security_tools.auth import require_jwt_role
from src.my_tools.security_tools.jwt_tools import create_token
from src.settings import SECURITY_CONFIG

from .models import User
from .schemas import LoginRequest, PasswordChangeRequest, RegisterRequest, UserOut

user_router = APIRouter(prefix="/user", tags=["user"])

# DB 不可用（连接失败 / 未初始化）统一映射为 503
# 注意：IntegrityError / DoesNotExist 等是业务异常，继承自 OperationalError，必须排除
_DB_UNAVAILABLE = (OperationalError, ConfigurationError)
_BUSINESS_ERRORS = (IntegrityError, DoesNotExist)


async def _safe(coro: Coroutine) -> Any:
    """执行 ORM 调用；DB 不可用类异常（含未初始化 RuntimeError）转 503，业务异常正常抛出"""
    try:
        return await coro
    except _BUSINESS_ERRORS:
        # 业务异常原样抛出，交给路由层处理（409 / 401 等）
        raise
    except _DB_UNAVAILABLE as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"Database unavailable: {exc.__class__.__name__}"
        ) from exc
    except RuntimeError as exc:
        if "TortoiseContext" not in str(exc):
            raise
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"Database unavailable: {exc.__class__.__name__}"
        ) from exc


def _issue_token(user: User) -> dict:
    token = create_token(
        {"sub": user.username, "role": user.role, "uid": user.id},
        SECURITY_CONFIG["jwt_secret"],
        algorithm=SECURITY_CONFIG["jwt_algorithm"],
        expires_seconds=SECURITY_CONFIG["jwt_expire_seconds"],
    )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": SECURITY_CONFIG["jwt_expire_seconds"],
    }


async def _get_user_or_404(payload: dict) -> User:
    try:
        user = await _safe(User.get(username=payload.get("sub", "")))
    except DoesNotExist as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists") from exc
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User disabled")
    return user


@user_router.post("/register", summary="注册新用户", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest) -> UserOut:
    try:
        user = await _safe(User.create(username=body.username, password_hash=hash_password(body.password)))
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Username already exists: {body.username}") from exc
    logger.bind(username=user.username).info(f"user registered: {user.username}")
    return UserOut.model_validate(user, from_attributes=True)


@user_router.post("/login", summary="登录并签发 JWT")
async def login(body: LoginRequest) -> dict:
    try:
        user = await _safe(User.get(username=body.username))
    except DoesNotExist as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password") from exc

    if not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    logger.bind(username=user.username).info(f"user login: {user.username}")
    return _issue_token(user)


@user_router.get("/me", summary="当前用户信息（Bearer JWT）")
async def me(payload: dict = Depends(verify_jwt)) -> UserOut:
    user = await _get_user_or_404(payload)
    return UserOut.model_validate(user, from_attributes=True)


@user_router.get("/list", summary="用户列表（admin 角色）", dependencies=[Depends(require_jwt_role("admin"))])
async def list_users(page: int = 1, page_size: int = 20) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    total = await _safe(User.all().count())
    users = await _safe(User.all().offset((page - 1) * page_size).limit(page_size).order_by("id"))
    items = [UserOut.model_validate(u, from_attributes=True) for u in users]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@user_router.put("/password", summary="修改自己的密码（Bearer JWT）")
async def change_password(body: PasswordChangeRequest, payload: dict = Depends(verify_jwt)) -> dict:
    user = await _get_user_or_404(payload)
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Old password incorrect")
    user.password_hash = hash_password(body.new_password)
    await _safe(user.save(update_fields=["password_hash", "modified_at"]))
    logger.bind(username=user.username).info(f"password changed: {user.username}")
    return {"message": "password updated"}
