from __future__ import annotations
from pydantic import BaseModel
from typing import Any


class StrategyInfo(BaseModel):
    name: str
    display_name: str
    description: str
    version: str
    default_params: dict[str, Any]
    tunable_params: dict[str, dict[str, Any]]


class StrategyConfigResponse(BaseModel):
    strategy_name: str
    is_enabled: bool
    params: dict[str, Any]
    strategy_info: StrategyInfo | None = None

    model_config = {"from_attributes": True}


class StrategyParamsUpdate(BaseModel):
    params: dict[str, Any]
