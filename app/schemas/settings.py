from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StrategyStateResponse(BaseModel):
    scope: str
    data: dict[str, str]


class AccountSettingsResponse(BaseModel):
    account_id: str
    strategy_states: dict[str, dict[str, str]]
    strategy_configs: list[dict[str, Any]]
