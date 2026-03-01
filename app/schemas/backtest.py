from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class BacktestComboConfig(BaseModel):
    name: str
    buy_logic_name: str
    buy_params: dict[str, Any] = {}
    sell_logic_name: str
    sell_params: dict[str, Any] = {}
    reference_combo_name: str | None = None


class BacktestRunRequest(BaseModel):
    symbol: str = "BTCUSDT"
    start_ts_ms: int
    end_ts_ms: int
    initial_usdt: float = 10000.0
    combos: list[BacktestComboConfig] = Field(default_factory=list)


class BacktestRunResponse(BaseModel):
    id: UUID
    status: str

    model_config = {"from_attributes": True}


class BacktestStatusResponse(BaseModel):
    id: UUID
    status: str
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class BacktestConfigOut(BaseModel):
    symbol: str
    combos: list[dict] | None = None
    # legacy fields for old backtest records
    strategies: list[str] | None = None
    strategy_params: dict | None = None
    initial_usdt: float
    start_ts_ms: int
    end_ts_ms: int


class BacktestSummaryOut(BaseModel):
    final_value_usdt: float
    pnl_usdt: float
    pnl_pct: float
    total_trades: int
    buy_trades: int = 0
    sell_trades: int = 0
    winning_trades: int
    losing_trades: int
    win_rate: float
    max_drawdown_pct: float
    profit_factor: float
    qty_before: float | None = None
    qty_after: float | None = None
    qty_change_pct: float | None = None
    max_open_lots: int = 0


class BacktestReportResponse(BaseModel):
    id: UUID
    config: BacktestConfigOut
    summary: BacktestSummaryOut | None = None
    trade_log: list[dict] | None = None
    equity_curve: list[dict] | None = None
    candles: list[dict] | None = None


class BacktestListItem(BaseModel):
    id: UUID
    symbol: str
    combos: list[dict] | None = None
    # legacy fields for old backtest records
    strategies: list[str] | None = None
    initial_usdt: float
    start_ts_ms: int
    end_ts_ms: int
    status: str
    pnl_pct: float | None = None
    created_at: datetime
    pinned: bool = False

    model_config = {"from_attributes": True}
