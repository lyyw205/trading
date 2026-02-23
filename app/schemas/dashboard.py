from __future__ import annotations
from pydantic import BaseModel
from typing import Any


class DashboardSummary(BaseModel):
    account_id: str
    account_name: str
    symbol: str
    current_price: float
    position: dict[str, Any] | None
    open_lots_count: int
    total_net_profit: float
    reserve_qty: float
    reserve_cost_usdt: float
    is_active: bool
    health: dict[str, Any]


class AssetStatus(BaseModel):
    symbol: str
    current_price: float
    position_qty: float
    position_cost: float
    position_avg_entry: float
    unrealized_pnl: float
    reserve_qty: float
    reserve_cost_usdt: float
    free_usdt: float


class TuneValues(BaseModel):
    strategy_name: str
    params: dict[str, Any]


class TuneUpdate(BaseModel):
    strategy_name: str
    params: dict[str, Any]
