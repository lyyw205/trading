from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any


class BuyPauseInfo(BaseModel):
    state: str  # ACTIVE, THROTTLED, PAUSED
    reason: str | None = None
    since: str | None = None  # ISO datetime string
    consecutive_low_balance: int = 0


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
    pending_earnings_usdt: float
    is_active: bool
    health: dict[str, Any]
    buy_pause: BuyPauseInfo


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


class ApproveEarningsRequest(BaseModel):
    reserve_pct: float = Field(ge=0, le=100)


class ApproveEarningsResponse(BaseModel):
    total_earnings: float
    to_reserve_usdt: float
    to_reserve_btc: float
    to_liquid_usdt: float
    reserve_pct: float
