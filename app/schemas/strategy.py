from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator


class BuyLogicInfo(BaseModel):
    name: str
    display_name: str
    description: str
    version: str
    default_params: dict[str, Any]
    tunable_params: dict[str, dict[str, Any]]


class SellLogicInfo(BaseModel):
    name: str
    display_name: str
    description: str
    version: str
    default_params: dict[str, Any]
    tunable_params: dict[str, dict[str, Any]]


# --- Combo schemas ---

class ComboCreate(BaseModel):
    name: str
    symbols: list[str]
    buy_logic_name: str
    buy_params: dict[str, Any] = {}
    sell_logic_name: str
    sell_params: dict[str, Any] = {}
    reference_combo_id: UUID | None = None

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("symbols must contain at least one entry")
        return [s.upper() for s in v]


class ComboUpdate(BaseModel):
    name: str | None = None
    symbols: list[str] | None = None
    buy_params: dict[str, Any] | None = None
    sell_params: dict[str, Any] | None = None
    reference_combo_id: UUID | None = None
    reapply_open_orders: bool = False


class ComboResponse(BaseModel):
    id: UUID
    name: str
    symbols: list[str]
    buy_logic_name: str
    buy_params: dict[str, Any]
    sell_logic_name: str
    sell_params: dict[str, Any]
    reference_combo_id: UUID | None
    is_enabled: bool

    model_config = {"from_attributes": True}
