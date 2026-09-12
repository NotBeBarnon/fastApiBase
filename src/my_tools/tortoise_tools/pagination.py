# @Description : 通用分页与查询参数工具（可复用到所有业务模块）
from __future__ import annotations

from typing import Any

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field


class PageResult[T](BaseModel):
    """统一分页响应格式"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码（从 1 开始）")
    page_size: int = Field(..., description="每页条数")
    items: list[T] = Field(..., description="当前页数据")


class PaginationParams:
    """分页查询参数依赖（page / page_size）"""

    def __init__(
        self,
        page: int = Query(1, ge=1, description="页码，从 1 开始"),
        page_size: int = Query(20, ge=1, le=100, description="每页条数，1-100"),
    ):
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class OrderByParams:
    """排序参数依赖基类（直接 Depends(OrderByParams) 用默认配置，或用 make_order_by_params(...) 自定义）"""

    _allowed_fields: set[str] | None = None
    _default_sort: str = "-created_at"

    def __init__(
        self,
        sort: str = Query("-created_at", description="排序字段，前缀 - 表示降序，如 -created_at / name"),
    ):
        self.raw_sort = sort or self._default_sort
        self.field, self.descending = self._parse(self.raw_sort)

    @classmethod
    def _parse(cls, sort: str) -> tuple[str, bool]:
        descending = sort.startswith("-")
        field = sort[1:] if descending else sort
        if not field:
            field = cls._default_sort.lstrip("-")
            descending = cls._default_sort.startswith("-")
        if cls._allowed_fields is not None and field not in cls._allowed_fields:
            # 非法字段回退到默认排序
            field = cls._default_sort.lstrip("-")
            descending = cls._default_sort.startswith("-")
        return field, descending

    @property
    def order_expr(self) -> str:
        """Tortoise .order_by() 表达式，如 '-created_at' 或 'name'"""
        return f"-{self.field}" if self.descending else self.field

    def apply(self, queryset: Any) -> Any:
        """直接对 Tortoise QuerySet 应用排序"""
        return queryset.order_by(self.order_expr)


def make_order_by_params(
    allowed_fields: set[str] | None = None,
    default_sort: str = "-created_at",
) -> type[OrderByParams]:
    """创建带自定义配置的 OrderByParams 子类，用于 Depends()"""
    return type(
        "CustomOrderByParams",
        (OrderByParams,),
        {"_allowed_fields": allowed_fields, "_default_sort": default_sort},
    )


__all__ = ("PageResult", "PaginationParams", "OrderByParams", "make_order_by_params")
