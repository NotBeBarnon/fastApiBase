# @Description : Admin 专属接口（仅 admin 角色）：增强用户列表 + 更新角色/状态
from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, Field
from tortoise.exceptions import ConfigurationError, DoesNotExist, OperationalError

from src.my_tools.security_tools import verify_jwt
from src.my_tools.security_tools.auth import require_jwt_role

from ..users.models import User
from ..users.schemas import UserOut

admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_jwt_role("admin"))])

_DB_UNAVAILABLE = (OperationalError, ConfigurationError)


async def _safe(coro: Coroutine) -> Any:
    try:
        return await coro
    except _DB_UNAVAILABLE as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Database unavailable: {exc.__class__.__name__}") from exc
    except RuntimeError as exc:
        if "TortoiseContext" not in str(exc):
            raise
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Database unavailable: {exc.__class__.__name__}") from exc


class UserUpdateRequest(BaseModel):
    role: Optional[str] = Field(default=None, description="角色：user / admin")
    is_active: Optional[bool] = Field(default=None, description="是否启用")

    model_config = {"json_schema_extra": {"example": {"role": "user", "is_active": True}}}


@admin_router.get("/users", summary="用户列表（管理员）")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    username: Optional[str] = Query(None, description="按用户名模糊搜索"),
    role: Optional[str] = Query(None, description="按角色过滤"),
    is_active: Optional[bool] = Query(None, description="按启用状态过滤"),
) -> dict:
    qs = User.all()
    if username:
        qs = qs.filter(username__icontains=username)
    if role:
        qs = qs.filter(role=role)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)

    total = await _safe(qs.count())
    users = await _safe(qs.offset((page - 1) * page_size).limit(page_size).order_by("id"))
    items = [UserOut.model_validate(u, from_attributes=True) for u in users]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@admin_router.put("/users/{user_id}", summary="更新用户角色/状态（管理员）", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdateRequest,
    payload: dict = Depends(verify_jwt),
) -> Any:
    try:
        user = await _safe(User.get(id=user_id))
    except DoesNotExist:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    current_id: int = payload.get("uid", 0)
    if user_id == current_id and body.is_active is False:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot disable yourself")
    if user_id == current_id and body.role and body.role != "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot demote yourself")

    patch: dict = {}
    if body.role is not None:
        if body.role not in ("user", "admin"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid role, must be user/admin")
        patch["role"] = body.role
    if body.is_active is not None:
        patch["is_active"] = body.is_active
    if not patch:
        return UserOut.model_validate(user, from_attributes=True)

    for k, v in patch.items():
        setattr(user, k, v)
    await _safe(user.save(update_fields=list(patch.keys()) + ["modified_at"]))
    logger.bind(actor_id=current_id, target_id=user_id, patch=patch).info("admin user updated")
    return UserOut.model_validate(user, from_attributes=True)
