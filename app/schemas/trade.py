from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel
from typing import Any


class LotResponse(BaseModel):
    lot_id: int
    account_id: UUID
    symbol: str
    strategy_name: str
    combo_id: UUID | None = None
    buy_price: float
    buy_qty: float
    buy_time: datetime | None
    buy_time_ms: int | None
    status: str
    sell_price: float | None
    sell_time: datetime | None
    fee_usdt: float | None
    net_profit_usdt: float | None

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    order_id: int
    account_id: UUID
    symbol: str
    side: str | None
    type: str | None
    status: str | None
    price: float | None
    orig_qty: float | None
    executed_qty: float | None
    cum_quote_qty: float | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class PositionResponse(BaseModel):
    account_id: UUID
    symbol: str
    qty: float
    cost_basis_usdt: float
    avg_entry: float

    model_config = {"from_attributes": True}
