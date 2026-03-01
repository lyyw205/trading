"""
Debug endpoint for account state inspection.
Admin-only, always available.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import GlobalConfig
from app.db.session import get_trading_session
from app.dependencies import require_admin
from app.models.account import TradingAccount

router = APIRouter(prefix="/api/debug", tags=["debug"])
logger = logging.getLogger(__name__)
settings = GlobalConfig()


@router.get("/account/{account_id}/state")
async def get_account_debug_state(
    account_id: UUID,
    request: Request,
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """
    Full account state snapshot for debugging.
    """
    # Get account from DB
    result = await session.execute(
        select(TradingAccount).where(TradingAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Get trading engine state
    engine = getattr(request.app.state, "trading_engine", None)
    engine_health = {}
    if engine:
        all_health = engine.get_account_health()
        engine_health = all_health.get(str(account_id), {})

    return {
        "account_id": str(account.id),
        "name": account.name,
        "state": {
            "is_active": account.is_active,
            "buy_pause_state": account.buy_pause_state,
            "buy_pause_reason": account.buy_pause_reason,
            "buy_pause_since": str(account.buy_pause_since) if account.buy_pause_since else None,
            "consecutive_low_balance": account.consecutive_low_balance,
            "circuit_breaker_failures": account.circuit_breaker_failures,
            "circuit_breaker_disabled_at": str(account.circuit_breaker_disabled_at) if account.circuit_breaker_disabled_at else None,
            "last_success_at": str(account.last_success_at) if account.last_success_at else None,
        },
        "config": {
            "loop_interval_sec": account.loop_interval_sec,
            "order_cooldown_sec": account.order_cooldown_sec,
            "symbol": account.symbol,
            "exchange": account.exchange,
        },
        "engine": engine_health,
    }
