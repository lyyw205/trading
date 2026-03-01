from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.lot_repo import LotRepository
from app.db.position_repo import PositionRepository
from app.db.price_repo import get_candles
from app.db.session import get_trading_session
from app.dependencies import get_owned_account, limiter
from app.models.core_btc_history import CoreBtcHistory
from app.models.lot import Lot
from app.models.order import Order
from app.schemas.dashboard import (
    ApproveEarningsRequest,
    ApproveEarningsResponse,
    BuyPauseInfo,
    DashboardSummary,
)
from app.schemas.trade import LotResponse, OrderResponse
from app.services.account_state_manager import AccountStateManager
from app.utils.logging import audit_log

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/{account_id}", response_model=DashboardSummary)
@limiter.limit("120/minute")
async def get_dashboard(
    request: Request,
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    # Position
    pos_repo = PositionRepository(session)
    position = await pos_repo.get(account.id, account.symbol)

    # Open lots count (all strategies)
    lot_repo = LotRepository(session)
    open_lots_stmt = select(func.count()).select_from(Lot).where(
        Lot.account_id == account.id, Lot.status == "OPEN"
    )
    open_lots_result = await session.execute(open_lots_stmt)
    open_lots_total = open_lots_result.scalar_one()

    # Total net profit from closed lots
    stmt = select(func.coalesce(func.sum(Lot.net_profit_usdt), 0)).where(
        Lot.account_id == account.id, Lot.status == "CLOSED"
    )
    result = await session.execute(stmt)
    total_profit = float(result.scalar_one())

    # Reserve from shared state
    account_state = AccountStateManager(account.id, session)
    reserve_qty = await account_state.get_reserve_qty()
    reserve_cost = await account_state.get_reserve_cost_usdt()

    # Pending earnings
    pending_earnings = await account_state.get_pending_earnings()

    # Health
    engine = request.app.state.trading_engine
    health = engine.get_account_health().get(str(account.id), {})

    # Current price
    price_collector = engine._price_collector
    cur_price = await price_collector.get_price(account.symbol)

    return DashboardSummary(
        account_id=str(account.id),
        account_name=account.name,
        symbol=account.symbol,
        current_price=cur_price,
        position={"qty": float(position.qty), "cost_basis_usdt": float(position.cost_basis_usdt), "avg_entry": float(position.avg_entry)} if position else None,
        open_lots_count=open_lots_total,
        total_net_profit=total_profit,
        reserve_qty=reserve_qty,
        reserve_cost_usdt=reserve_cost,
        pending_earnings_usdt=pending_earnings,
        is_active=account.is_active,
        health=health,
        buy_pause=BuyPauseInfo(
            state=account.buy_pause_state or "ACTIVE",
            reason=account.buy_pause_reason,
            since=account.buy_pause_since.isoformat() if account.buy_pause_since else None,
            consecutive_low_balance=account.consecutive_low_balance or 0,
        ),
    )


@router.get("/{account_id}/pending_earnings")
@limiter.limit("120/minute")
async def get_pending_earnings(
    request: Request,
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    account_state = AccountStateManager(account.id, session)
    earnings = await account_state.get_pending_earnings()
    return {"pending_earnings_usdt": earnings}


@router.post("/{account_id}/approve_earnings", response_model=ApproveEarningsResponse)
@limiter.limit("10/minute")
async def approve_earnings(
    request: Request,
    body: ApproveEarningsRequest,
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    # current_price: PriceCollector 캐시에서 가져옴
    engine = request.app.state.trading_engine
    price_collector = engine._price_collector
    current_price = await price_collector.get_price(account.symbol)

    if current_price <= 0:
        raise HTTPException(status_code=503, detail="현재가를 가져올 수 없습니다.")

    account_state = AccountStateManager(account.id, session)

    try:
        result = await account_state.approve_earnings_to_reserve(
            pct=body.reserve_pct,
            current_price=current_price,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # core_btc_history 이력 기록
    if result["to_reserve_usdt"] > 0:
        history = CoreBtcHistory(
            account_id=account.id,
            symbol=account.symbol,
            btc_qty=result["to_reserve_btc"],
            cost_usdt=result["to_reserve_usdt"],
            source="MANUAL_APPROVE",
        )
        session.add(history)

    await session.commit()

    # audit_log 기록
    user = getattr(request.state, "user", {})
    audit_log(
        "approve_earnings",
        user_id=user.get("sub", "unknown") if isinstance(user, dict) else "unknown",
        account_id=str(account.id),
        reserve_pct=body.reserve_pct,
        total_earnings=result["total_earnings"],
        to_reserve_usdt=result["to_reserve_usdt"],
        to_liquid_usdt=result["to_liquid_usdt"],
    )

    return ApproveEarningsResponse(**result)


@router.get("/{account_id}/lots", response_model=list[LotResponse])
@limiter.limit("120/minute")
async def get_lots(
    request: Request,
    strategy: str | None = None,
    combo_id: UUID | None = None,
    status: str = "OPEN",
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    filters = [Lot.account_id == account.id, Lot.status == status]
    if combo_id:
        filters.append(Lot.combo_id == combo_id)
    elif strategy:
        filters.append(Lot.strategy_name == strategy)
    stmt = select(Lot).where(*filters).order_by(Lot.lot_id.desc()).limit(100)
    result = await session.execute(stmt)
    return [LotResponse.model_validate(l) for l in result.scalars().all()]


@router.get("/{account_id}/trades", response_model=list[OrderResponse])
@limiter.limit("120/minute")
async def get_trades(
    request: Request,
    limit: int = Query(default=50, le=200),
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    stmt = select(Order).where(Order.account_id == account.id).order_by(Order.update_time_ms.desc()).limit(limit)
    result = await session.execute(stmt)
    return [OrderResponse.model_validate(o) for o in result.scalars().all()]


@router.get("/{account_id}/price_candles")
@limiter.limit("120/minute")
async def get_price_candles(
    request: Request,
    from_ms: int = 0,
    to_ms: int = 0,
    interval: str = Query(default="5m"),
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    candles = await get_candles(account.symbol, from_ms, to_ms, session, interval=interval)
    result = []
    for c in candles:
        d = {"ts_ms": c.ts_ms, "open": float(c.open), "high": float(c.high), "low": float(c.low), "close": float(c.close)}
        if hasattr(c, "volume"):
            d["volume"] = float(c.volume)
        result.append(d)
    return result
