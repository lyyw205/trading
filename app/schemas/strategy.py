from __future__ import annotations
from uuid import UUID
from pydantic import BaseModel
from typing import Any


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
    buy_logic_name: str
    buy_params: dict[str, Any] = {}
    sell_logic_name: str
    sell_params: dict[str, Any] = {}
    reference_combo_id: UUID | None = None


class ComboUpdate(BaseModel):
    name: str | None = None
    buy_params: dict[str, Any] | None = None
    sell_params: dict[str, Any] | None = None
    reference_combo_id: UUID | None = None


class ComboResponse(BaseModel):
    id: UUID
    name: str
    buy_logic_name: str
    buy_params: dict[str, Any]
    sell_logic_name: str
    sell_params: dict[str, Any]
    reference_combo_id: UUID | None
    is_enabled: bool

    model_config = {"from_attributes": True}
