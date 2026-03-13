from __future__ import annotations

import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.db.position_repo import PositionRepository
from app.db.price_repo import get_candles
from app.db.session import get_trading_session
from app.dependencies import get_owned_account, limiter
from app.models.core_btc_history import CoreBtcHistory
from app.models.fill import Fill
from app.models.lot import Lot
from app.models.order import Order
from app.models.position import Position
from app.schemas.dashboard import (
    ApproveEarningsRequest,
    ApproveEarningsResponse,
    AssetStatus,
    BuyPauseInfo,
    DashboardSummary,
    HeldSymbol,
    PositionInfo,
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
    open_lots_stmt = select(func.count()).select_from(Lot).where(Lot.account_id == account.id, Lot.status == "OPEN")
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
    cur_price = await engine.get_current_price(account.symbol)

    return DashboardSummary(
        account_id=account.id,
        account_name=account.name,
        symbol=account.symbol,
        current_price=cur_price,
        position=PositionInfo(
            qty=float(position.qty),
            cost_basis_usdt=float(position.cost_basis_usdt),
            avg_entry=float(position.avg_entry),
        )
        if position
        else None,
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
    current_price = await engine.get_current_price(account.symbol)

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
        user_id=user.get("id", "unknown") if isinstance(user, dict) else "unknown",
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
    status: Literal["OPEN", "CLOSED", "CANCELLED", "MERGED"] = "OPEN",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    filters = [Lot.account_id == account.id, Lot.status == status]
    if combo_id:
        filters.append(Lot.combo_id == combo_id)
    elif strategy:
        filters.append(Lot.strategy_name == strategy)
    stmt = (
        select(Lot)
        .options(defer(Lot.metadata_))
        .where(*filters)
        .order_by(Lot.lot_id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    lots_list = list(result.scalars().all())

    # Enrich with computed fields
    engine = request.app.state.trading_engine
    price_cache: dict[str, float] = {}
    # Batch fetch sell order statuses
    sell_order_ids = [lot.sell_order_id for lot in lots_list if lot.sell_order_id]
    sell_order_map: dict[int, str] = {}
    if sell_order_ids:
        order_stmt = select(Order.order_id, Order.status).where(
            Order.account_id == account.id, Order.order_id.in_(sell_order_ids)
        )
        order_rows = await session.execute(order_stmt)
        sell_order_map = {row.order_id: row.status for row in order_rows}

    responses = []
    for lot in lots_list:
        sym = lot.symbol
        if sym not in price_cache:
            try:
                price_cache[sym] = await engine.get_current_price(sym)
            except Exception:
                price_cache[sym] = 0.0
        cur_price = price_cache[sym]
        buy_price = float(lot.buy_price)
        buy_qty = float(lot.buy_qty)
        cost_usdt = round(buy_price * buy_qty, 2)
        pnl_pct = round((cur_price - buy_price) / buy_price * 100, 2) if buy_price > 0 else None

        sell_status = None
        if lot.sell_order_id:
            sell_status = sell_order_map.get(lot.sell_order_id, "UNKNOWN")

        resp = LotResponse.model_validate(lot)
        resp.strategy = lot.strategy_name
        resp.qty = buy_qty
        resp.cost_usdt = cost_usdt
        resp.current_price = cur_price
        resp.pnl_pct = pnl_pct
        resp.sell_order_status = sell_status
        responses.append(resp)

    return responses


@router.get("/{account_id}/trades", response_model=list[OrderResponse])
@limiter.limit("120/minute")
async def get_trades(
    request: Request,
    limit: int = Query(default=50, le=200),
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    stmt = (
        select(Order)
        .options(defer(Order.raw_json))
        .where(Order.account_id == account.id)
        .order_by(Order.update_time_ms.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [OrderResponse.model_validate(o) for o in result.scalars().all()]


@router.get("/{account_id}/price_candles")
@limiter.limit("120/minute")
async def get_price_candles(
    request: Request,
    from_ms: int = 0,
    to_ms: int = 0,
    interval: Literal["1m", "5m", "1h", "1d"] = Query(default="5m"),
    symbol: str | None = Query(default=None),
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    target_symbol = symbol or account.symbol

    # Default time range when not specified: recent candles based on interval
    if to_ms <= 0:
        to_ms = int(time.time() * 1000)
    if from_ms <= 0:
        default_spans = {"1m": 6 * 3600_000, "5m": 24 * 3600_000, "1h": 7 * 86400_000, "1d": 90 * 86400_000}
        from_ms = to_ms - default_spans.get(interval, 24 * 3600_000)

    candles = await get_candles(target_symbol, from_ms, to_ms, session, interval=interval)

    # Fallback: if no candles in default range, fetch whatever exists (last 500)
    if not candles:
        from app.models.price_candle import PriceCandle1d, PriceCandle1h, PriceCandle1m, PriceCandle5m

        _models = {"1m": PriceCandle1m, "5m": PriceCandle5m, "1h": PriceCandle1h, "1d": PriceCandle1d}
        model = _models.get(interval, PriceCandle1m)
        fallback_stmt = select(model).where(model.symbol == target_symbol).order_by(model.ts_ms.desc()).limit(500)
        fallback_result = await session.execute(fallback_stmt)
        candles = list(reversed(fallback_result.scalars().all()))

    # Fallback: if higher-TF table is empty, aggregate from 1m on the fly
    if not candles and interval != "1m":
        raw = await get_candles(target_symbol, from_ms, to_ms, session, interval="1m")
        if raw:
            bucket_sec = {"5m": 300, "1h": 3600, "1d": 86400}[interval]
            bucket_ms = bucket_sec * 1000
            buckets: dict[int, dict] = {}
            for c in raw:
                key = (c.ts_ms // bucket_ms) * bucket_ms
                if key not in buckets:
                    buckets[key] = {
                        "ts_ms": key,
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": float(c.volume) if hasattr(c, "volume") else 0.0,
                    }
                else:
                    b = buckets[key]
                    b["high"] = max(b["high"], float(c.high))
                    b["low"] = min(b["low"], float(c.low))
                    b["close"] = float(c.close)
                    if hasattr(c, "volume"):
                        b["volume"] = b.get("volume", 0.0) + float(c.volume)
            return sorted(buckets.values(), key=lambda x: x["ts_ms"])

    result = []
    for c in candles:
        d = {
            "ts_ms": c.ts_ms,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
        }
        if hasattr(c, "volume"):
            d["volume"] = float(c.volume)
        result.append(d)
    return result


@router.get("/{account_id}/asset_status", response_model=AssetStatus)
@limiter.limit("120/minute")
async def get_asset_status(
    request: Request,
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    """Return asset balances, reserve pool, pending earnings, and total invested."""
    engine = request.app.state.trading_engine

    # Fetch all positions for this account
    pos_stmt = select(Position).where(Position.account_id == account.id, Position.qty > 0)
    pos_result = await session.execute(pos_stmt)
    positions = pos_result.scalars().all()

    # Build held_symbols list with current prices
    held_symbols: list[HeldSymbol] = []
    total_base_value_usdt = 0.0
    for pos in positions:
        qty = float(pos.qty)
        avg_entry = float(pos.avg_entry)
        cost_basis = float(pos.cost_basis_usdt)
        try:
            cur_price = await engine.get_current_price(pos.symbol)
        except Exception:
            cur_price = 0.0
        value_usdt = round(qty * cur_price, 2) if cur_price > 0 else 0.0
        pnl_usdt = round(value_usdt - cost_basis, 2)
        pnl_pct = round(pnl_usdt / cost_basis * 100, 2) if cost_basis > 0 else 0.0
        total_base_value_usdt += value_usdt
        held_symbols.append(
            HeldSymbol(
                symbol=pos.symbol,
                qty=qty,
                avg_entry=round(avg_entry, 4),
                current_price=round(cur_price, 4),
                value_usdt=value_usdt,
                pnl_usdt=pnl_usdt,
                pnl_pct=pnl_pct,
            )
        )

    # Legacy btc_balance: primary symbol position
    primary_pos = next((p for p in positions if p.symbol == account.symbol), None)
    base_qty = float(primary_pos.qty) if primary_pos else 0.0
    primary_price = await engine.get_current_price(account.symbol) if not held_symbols else 0.0
    if held_symbols:
        primary_price = next((h.current_price for h in held_symbols if h.symbol == account.symbol), 0.0)

    # Reserve pool
    account_state = AccountStateManager(account.id, session)
    reserve_qty = await account_state.get_reserve_qty()
    reserve_cost = await account_state.get_reserve_cost_usdt()

    # Pending earnings
    pending_earnings = await account_state.get_pending_earnings()

    # Total invested = cost basis of open lots
    stmt = select(func.coalesce(func.sum(Lot.buy_price * Lot.buy_qty), 0)).where(
        Lot.account_id == account.id, Lot.status == "OPEN"
    )
    result = await session.execute(stmt)
    total_invested = float(result.scalar_one())

    reserve_pct = min(round(reserve_cost / total_invested * 100, 1), 999.9) if total_invested > 1.0 else 0

    return AssetStatus(
        btc_balance=base_qty,
        usdt_balance=round(base_qty * primary_price, 2) if primary_price > 0 else 0,
        held_symbols=held_symbols,
        reserve_pool_qty=reserve_qty,
        reserve_pool_usdt=reserve_cost,
        reserve_pool_pct=reserve_pct,
        pending_earnings_usdt=pending_earnings,
        total_invested_usdt=round(total_invested, 2),
    )


@router.get("/{account_id}/trade_events")
@limiter.limit("120/minute")
async def get_trade_events(
    request: Request,
    limit: int = Query(default=200, le=500),
    symbol: str | None = Query(default=None),
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    """Return recent fills as chart markers (time, side, price)."""
    filters = [Fill.account_id == account.id]
    if symbol:
        filters.append(Fill.symbol == symbol)
    stmt = select(Fill).options(defer(Fill.raw_json)).where(*filters).order_by(Fill.trade_time_ms.desc()).limit(limit)
    result = await session.execute(stmt)
    fills = result.scalars().all()

    events = []
    for f in fills:
        if not f.trade_time_ms:
            continue
        events.append(
            {
                "time": f.trade_time_ms // 1000,  # lightweight-charts expects unix seconds
                "side": (f.side or "").lower(),
                "price": float(f.price) if f.price is not None else 0.0,
            }
        )
    # Sort ascending for chart markers
    events.sort(key=lambda e: e["time"])
    return events
