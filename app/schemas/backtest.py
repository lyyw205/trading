from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BacktestRunRequest(BaseModel):
    symbol: str = "BTCUSDT"
    start_ts_ms: int
    end_ts_ms: int
    initial_usdt: float = 10000.0
    strategies: list[str] = Field(default=["lot_stacking"])
    strategy_params: dict[str, dict] = Field(default_factory=dict)


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
    strategies: list[str]
    strategy_params: dict
    initial_usdt: float
    start_ts_ms: int
    end_ts_ms: int


class BacktestSummaryOut(BaseModel):
    final_value_usdt: float
    pnl_usdt: float
    pnl_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    max_drawdown_pct: float
    profit_factor: float


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
    strategies: list[str]
    initial_usdt: float
    start_ts_ms: int
    end_ts_ms: int
    status: str
    pnl_pct: float | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
