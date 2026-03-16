from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class BuyPauseInfo(BaseModel):
    state: str  # ACTIVE, THROTTLED, PAUSED
    reason: str | None = None
    since: str | None = None  # ISO datetime string
    consecutive_low_balance: int = 0


class PositionInfo(BaseModel):
    qty: float
    cost_basis_usdt: float
    avg_entry: float


class DashboardSummary(BaseModel):
    account_id: UUID
    account_name: str
    symbol: str
    current_price: float
    position: PositionInfo | None
    open_lots_count: int
    total_net_profit: float
    reserve_qty: float
    reserve_cost_usdt: float
    pending_earnings_usdt: float
    is_active: bool
    health: dict[str, Any]
    buy_pause: BuyPauseInfo


class HeldSymbol(BaseModel):
    symbol: str
    qty: float
    avg_entry: float
    current_price: float
    value_usdt: float
    pnl_usdt: float
    pnl_pct: float


class OpenLotSymbol(BaseModel):
    symbol: str
    count: int
    oldest_buy_time: str  # ISO datetime
    holding_hours: float


class AssetStatus(BaseModel):
    btc_balance: float
    usdt_balance: float
    held_symbols: list[HeldSymbol] = []
    reserve_pool_qty: float
    reserve_pool_usdt: float
    reserve_pool_pct: float
    pending_earnings_usdt: float
    total_invested_usdt: float
    realized_pnl_today: float = 0.0
    realized_pnl_week: float = 0.0
    closed_lots_today: int = 0
    closed_lots_week: int = 0
    open_lots_by_symbol: list[OpenLotSymbol] = []


class ApproveEarningsRequest(BaseModel):
    reserve_pct: float = Field(ge=0, le=100)


class ApproveEarningsResponse(BaseModel):
    total_earnings: float
    to_reserve_usdt: float
    to_reserve_btc: float
    to_liquid_usdt: float
    reserve_pct: float
