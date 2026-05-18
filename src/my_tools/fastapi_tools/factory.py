# -*- coding: utf-8 -*-
# @Description : 自动生成基础 CRUD 视图（Pydantic v2 + Tortoise 0.21+）
from __future__ import annotations

from tortoise import Model
from tortoise.contrib.fastapi import HTTPNotFoundError
from tortoise.contrib.pydantic import PydanticModel

from .decorators import Action


def generate_all(model: type[Model], schema: type[PydanticModel]):
    """生成视图集的 all 方法"""

    @Action.get(f"/{model.__name__.lower()}s", response_model=list[schema])
    async def all(self):
        return await schema.from_queryset(model.all())

    all.__doc__ = f"Query all {model.__name__}"
    return all


def generate_create(model: type[Model], schema: type[PydanticModel], input_schema: type[PydanticModel]):
    """生成视图集的 create 方法"""

    @Action.post(f"/{model.__name__.lower()}", response_model=schema)
    async def create(self, body: input_schema):
        return await schema.from_tortoise_orm(await model.create(**body.model_dump()))

    create.__doc__ = f"Create {model.__name__}"
    return create


def generate_get(model: type[Model], schema: type[PydanticModel], pk_type: type):
    """生成视图集的 get 方法"""

    @Action.get(
        f"/{model.__name__.lower()}/{{pk}}",
        response_model=schema,
        responses={404: {"model": HTTPNotFoundError}},
    )
    async def get(self, pk: pk_type):
        return await schema.from_queryset_single(model.get(pk=pk))

    get.__doc__ = f"Get {model.__name__} by primary key"
    return get


def generate_update(
    model: type[Model],
    schema: type[PydanticModel],
    pk_type: type,
    input_schema: type[PydanticModel],
):
    """生成视图集的 update 方法"""

    @Action.patch(
        f"/{model.__name__.lower()}/{{pk}}",
        response_model=schema,
        responses={404: {"model": HTTPNotFoundError}},
    )
    async def update(self, pk: pk_type, body: input_schema):
        await model.filter(pk=pk).update(**body.model_dump(exclude_unset=True))
        return await schema.from_queryset_single(model.get(pk=pk))

    update.__doc__ = f"Update {model.__name__} by primary key"
    return update


def generate_delete(model: type[Model], schema: type[PydanticModel], pk_type: type):
    """生成视图集的 delete 方法"""

    @Action.delete(
        f"/{model.__name__.lower()}/{{pk}}",
        response_model=schema,
        responses={404: {"model": HTTPNotFoundError}},
    )
    async def delete(self, pk: pk_type):
        obj = await model.get(pk=pk)
        await model.filter(pk=pk).delete()
        return await schema.from_tortoise_orm(obj)

    delete.__doc__ = f"Delete {model.__name__} by primary key"
    return delete
