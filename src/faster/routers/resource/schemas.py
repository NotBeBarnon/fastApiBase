# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import ConfigDict
from tortoise.contrib.pydantic import pydantic_model_creator

from .models import Beam, BeamTypeEnum

BeamSchema = pydantic_model_creator(Beam, name="BeamSchema")
BeamCreateSchema = pydantic_model_creator(Beam, name="BeamCreateSchema", exclude=("id",), exclude_readonly=True)


class BeamUpdateSchema(
    pydantic_model_creator(Beam, name="BeamUpdateSchema", exclude=("id",), exclude_readonly=True)
):
    model_config = ConfigDict(title="BeamUpdateSchema")

    name: str | None = None
    type: BeamTypeEnum | None = None
