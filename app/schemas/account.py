from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    api_key: str = Field(..., min_length=1)
    api_secret: str = Field(..., min_length=1)
    symbol: str = Field(default="ETHUSDT")
    base_asset: str = Field(default="ETH")
    quote_asset: str = Field(default="USDT")
    loop_interval_sec: int = Field(default=60, ge=10, le=3600)
    order_cooldown_sec: int = Field(default=7, ge=1, le=300)
    owner_id: UUID | None = None


class AccountUpdate(BaseModel):
    name: str | None = None
    api_key: str | None = None
    api_secret: str | None = None
    symbol: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    loop_interval_sec: int | None = Field(None, ge=10, le=3600)
    order_cooldown_sec: int | None = Field(None, ge=1, le=300)
    is_active: bool | None = None


class AccountResponse(BaseModel):
    id: UUID
    name: str
    exchange: str
    symbol: str
    base_asset: str
    quote_asset: str
    is_active: bool
    circuit_breaker_failures: int
    circuit_breaker_disabled_at: datetime | None
    last_success_at: datetime | None
    loop_interval_sec: int
    order_cooldown_sec: int
    buy_pause_state: str = "ACTIVE"
    buy_pause_reason: str | None = None
    buy_pause_since: datetime | None = None
    consecutive_low_balance: int = 0
    created_at: datetime
    updated_at: datetime
    circuit_breaker_tripped: bool = False
    owner_id: UUID
    owner_email: str | None = None

    model_config = {"from_attributes": True}


class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
