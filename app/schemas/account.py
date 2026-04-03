from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    api_key: str = Field(default="", min_length=0)
    api_secret: str = Field(default="", min_length=0)
    symbol: str = Field(default="ETHUSDT")
    base_asset: str = Field(default="ETH")
    quote_asset: str = Field(default="USDT")
    loop_interval_sec: int = Field(default=60, ge=10, le=3600)
    order_cooldown_sec: int = Field(default=7, ge=1, le=300)
    owner_id: UUID | None = None
    is_paper: bool = False
    paper_initial_balance: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _validate_keys_or_paper(self) -> AccountCreate:
        """api_key/api_secret 필수 — 단, 페이퍼 계정은 더미값 허용."""
        if self.is_paper:
            if not self.api_key:
                self.api_key = "PAPER"
            if not self.api_secret:
                self.api_secret = "PAPER"
        else:
            if not self.api_key or not self.api_secret:
                raise ValueError("api_key and api_secret are required for live accounts")
        return self


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
    owner_id: UUID | None = None


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
    is_paper: bool = False
    paper_initial_balance: float = 0.0
    created_at: datetime
    updated_at: datetime
    circuit_breaker_tripped: bool = False
    owner_id: UUID
    owner_email: str | None = None  # Computed: joined from UserProfile.email
    combo_symbols: list[str] = []  # Computed: aggregated from TradingCombo.symbols

    model_config = {"from_attributes": True}


class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
