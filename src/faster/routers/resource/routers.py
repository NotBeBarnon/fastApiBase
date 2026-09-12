# @Description : 资源模块路由（Beam CRUD + 分页 + 过滤 + 权限隔离 + DB 降级）
from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from loguru import logger
from tortoise.exceptions import ConfigurationError, DoesNotExist, IntegrityError, OperationalError
from tortoise.queryset import QuerySet

from src.my_tools.security_tools import verify_jwt
from src.my_tools.security_tools.auth import require_jwt_role
from src.my_tools.tortoise_tools.pagination import OrderByParams, PageResult, PaginationParams, make_order_by_params

from .models import Beam, BeamTypeEnum
from .schemas import BeamCreate, BeamOut, BeamUpdate

resource_router = APIRouter(prefix="/resource", tags=["resource"])

# DB 不可用异常（业务异常排除在外）
_DB_UNAVAILABLE = (OperationalError, ConfigurationError)
_BUSINESS_ERRORS = (IntegrityError, DoesNotExist)

# 排序白名单
_ALLOWED_SORT_FIELDS = {"id", "name", "type", "created_at", "modified_at"}
_BeamOrderBy = make_order_by_params(allowed_fields=_ALLOWED_SORT_FIELDS, default_sort="-created_at")


async def _safe(coro: Coroutine) -> Any:
    """ORM 调用安全包装：DB 不可用转 503，业务异常原样抛出"""
    try:
        return await coro
    except _BUSINESS_ERRORS:
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


def _current_user_id(payload: dict) -> int:
    """从 JWT payload 取用户 ID，缺失抛 401"""
    uid = payload.get("uid")
    if not isinstance(uid, int):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token: uid missing")
    return uid


def _is_admin(payload: dict) -> bool:
    return payload.get("role") == "admin"


def _scoped_query(payload: dict) -> QuerySet[Beam]:
    """根据角色返回作用域查询集：admin 看全部，普通用户只看自己的"""
    qs = Beam.all()
    if not _is_admin(payload):
        qs = qs.filter(owner_id=_current_user_id(payload))
    return qs


async def _get_beam_or_404(payload: dict, beam_id: int) -> Beam:
    """按 ID 获取 beam，校验权限，不存在/越权均 404（避免枚举探测）"""
    qs = _scoped_query(payload)
    try:
        return await _safe(qs.get(id=beam_id))
    except DoesNotExist as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Beam {beam_id} not found") from exc


# ————————————————————————————————————————
#  CRUD 端点
# ————————————————————————————————————————

@resource_router.get(
    "/beams",
    summary="分页查询波束列表（Bearer JWT）",
    response_model=PageResult[BeamOut],
)
async def list_beams(
    page: PaginationParams = Depends(),
    sort: OrderByParams = Depends(_BeamOrderBy),
    beam_type: BeamTypeEnum | None = Query(None, description="按类型过滤"),
    name_like: str | None = Query(None, min_length=1, max_length=50, description="按名称模糊搜索"),
    payload: dict = Depends(verify_jwt),
) -> PageResult[BeamOut]:
    qs = _scoped_query(payload)

    if beam_type is not None:
        qs = qs.filter(type=beam_type)
    if name_like:
        qs = qs.filter(name__icontains=name_like)

    total = await _safe(qs.count())
    qs = sort.apply(qs)
    items = await _safe(qs.offset(page.offset).limit(page.limit))
    return PageResult(
        total=total,
        page=page.page,
        page_size=page.page_size,
        items=[BeamOut.model_validate(b, from_attributes=True) for b in items],
    )


@resource_router.get(
    "/beams/{beam_id}",
    summary="获取单个波束详情（Bearer JWT）",
    response_model=BeamOut,
    responses={404: {"description": "Beam not found"}},
)
async def get_beam(beam_id: int, payload: dict = Depends(verify_jwt)) -> BeamOut:
    beam = await _get_beam_or_404(payload, beam_id)
    return BeamOut.model_validate(beam, from_attributes=True)


@resource_router.post(
    "/beams",
    summary="创建波束（Bearer JWT）",
    response_model=BeamOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_beam(body: BeamCreate, payload: dict = Depends(verify_jwt)) -> BeamOut:
    uid = _current_user_id(payload)
    try:
        beam = await _safe(
            Beam.create(
                name=body.name,
                type=body.type,
                owner_id=uid,
                is_active=body.is_active,
            )
        )
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Beam creation conflict: {exc}") from exc
    logger.bind(username=payload.get("sub"), beam_id=beam.id).info(f"beam created: {beam.name}")
    return BeamOut.model_validate(beam, from_attributes=True)


@resource_router.put(
    "/beams/{beam_id}",
    summary="更新波束（Bearer JWT）",
    response_model=BeamOut,
    responses={404: {"description": "Beam not found"}},
)
async def update_beam(
    beam_id: int,
    body: BeamUpdate,
    payload: dict = Depends(verify_jwt),
) -> BeamOut:
    beam = await _get_beam_or_404(payload, beam_id)
    update_data = body.model_dump(exclude_unset=True)
    if update_data:
        for key, val in update_data.items():
            setattr(beam, key, val)
        await _safe(beam.save(update_fields=list(update_data.keys()) + ["modified_at"]))
    logger.bind(username=payload.get("sub"), beam_id=beam.id).info(f"beam updated: {beam.name}")
    return BeamOut.model_validate(beam, from_attributes=True)


@resource_router.delete(
    "/beams/{beam_id}",
    summary="删除波束（Bearer JWT）",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Beam not found"}},
    response_class=Response,
)
async def delete_beam(beam_id: int, payload: dict = Depends(verify_jwt)) -> Response:
    beam = await _get_beam_or_404(payload, beam_id)
    await _safe(beam.delete())
    logger.bind(username=payload.get("sub"), beam_id=beam_id).info(f"beam deleted: {beam_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ————————————————————————————————————————
#  管理员端点
# ————————————————————————————————————————

@resource_router.get(
    "/admin/beams",
    summary="管理员：全量波束列表（admin 角色）",
    response_model=PageResult[BeamOut],
    dependencies=[Depends(require_jwt_role("admin"))],
)
async def admin_list_beams(
    page: PaginationParams = Depends(),
    sort: OrderByParams = Depends(_BeamOrderBy),
    beam_type: BeamTypeEnum | None = Query(None, description="按类型过滤"),
    owner_id: int | None = Query(None, description="按所属用户 ID 过滤"),
    name_like: str | None = Query(None, min_length=1, max_length=50, description="按名称模糊搜索"),
) -> PageResult[BeamOut]:
    qs = Beam.all()
    if beam_type is not None:
        qs = qs.filter(type=beam_type)
    if owner_id is not None:
        qs = qs.filter(owner_id=owner_id)
    if name_like:
        qs = qs.filter(name__icontains=name_like)

    total = await _safe(qs.count())
    qs = sort.apply(qs)
    items = await _safe(qs.offset(page.offset).limit(page.limit))
    return PageResult(
        total=total,
        page=page.page,
        page_size=page.page_size,
        items=[BeamOut.model_validate(b, from_attributes=True) for b in items],
    )
