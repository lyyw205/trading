from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
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
    OpenLotSymbol,
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


@router.get("/{account_id}/pending-earnings")
@limiter.limit("120/minute")
async def get_pending_earnings(
    request: Request,
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    account_state = AccountStateManager(account.id, session)
    earnings = await account_state.get_pending_earnings()
    return {"pending_earnings_usdt": earnings}


@router.post("/{account_id}/approve-earnings", response_model=ApproveEarningsResponse)
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
        raise HTTPException(status_code=503, detail="Unable to retrieve current price.")

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
    # Batch fetch sell order statuses and prices
    sell_order_ids = [lot.sell_order_id for lot in lots_list if lot.sell_order_id]
    sell_order_map: dict[int, tuple[str, float | None]] = {}
    if sell_order_ids:
        order_stmt = select(Order.order_id, Order.status, Order.price).where(
            Order.account_id == account.id, Order.order_id.in_(sell_order_ids)
        )
        order_rows = await session.execute(order_stmt)
        sell_order_map = {
            row.order_id: (row.status, float(row.price) if row.price is not None else None) for row in order_rows
        }

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
        sell_order_price = None
        if lot.sell_order_id:
            order_info = sell_order_map.get(lot.sell_order_id)
            if order_info is not None:
                sell_status, sell_order_price = order_info
            else:
                sell_status = "UNKNOWN"

        resp = LotResponse.model_validate(lot)
        resp.strategy = lot.strategy_name
        resp.qty = buy_qty
        resp.cost_usdt = cost_usdt
        resp.current_price = cur_price
        resp.pnl_pct = pnl_pct
        resp.sell_order_status = sell_status
        resp.sell_order_price = sell_order_price
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
        .order_by(Order.order_id.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [OrderResponse.model_validate(o) for o in result.scalars().all()]


def _candle_to_dict(c) -> dict:
    """ORM 캔들 → dict 변환."""
    d = {"ts_ms": c.ts_ms, "open": float(c.open), "high": float(c.high), "low": float(c.low), "close": float(c.close)}
    if hasattr(c, "volume"):
        d["volume"] = float(c.volume)
    return d


@router.get("/{account_id}/price-candles")
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

    # 1m은 그대로 테이블 조회
    if interval == "1m":
        candles = await get_candles(target_symbol, from_ms, to_ms, session, interval="1m")
        return [_candle_to_dict(c) for c in candles]

    # 5m/1h/1d: 기존 집계 테이블 + 최근 1m 실시간 집계 병합
    # - 기존 테이블: CandleAggregator가 배치로 집계한 과거 데이터
    # - 1m 실시간 집계: 아직 배치 집계되지 않은 최근 데이터를 read-time에 계산
    #
    # TODO: 유저 100명+ 동시 접속 시 부하가 발생하면 아래 방안 검토:
    # 1) 심볼+타임프레임별 인메모리 캐시 (TTL 5분)
    # 2) CandleAggregator 주기를 6시간 → 5분으로 단축 (DB 저장량 증가)
    # 3) API 응답에 Cache-Control 헤더 추가 (브라우저 캐시)

    bucket_ms = {"5m": 300_000, "1h": 3_600_000, "1d": 86_400_000}[interval]
    buckets: dict[int, dict] = {}

    # Step 1: 기존 집계 테이블에서 조회
    pre_agg = await get_candles(target_symbol, from_ms, to_ms, session, interval=interval)
    for c in pre_agg:
        buckets[c.ts_ms] = _candle_to_dict(c)

    # Step 2: 1m 데이터에서 실시간 집계 (아직 배치 집계 안 된 구간 포함)
    pre_agg_keys = {c.ts_ms for c in pre_agg}
    raw_1m = await get_candles(target_symbol, from_ms, to_ms, session, interval="1m")
    for c in raw_1m:
        key = (c.ts_ms // bucket_ms) * bucket_ms
        # 기존 집계 데이터가 있는 버킷은 건너뜀 (이미 정확)
        if key in pre_agg_keys:
            continue
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
            bucket = buckets[key]
            bucket["high"] = max(bucket["high"], float(c.high))
            bucket["low"] = min(bucket["low"], float(c.low))
            bucket["close"] = float(c.close)
            if hasattr(c, "volume"):
                bucket["volume"] = bucket.get("volume", 0.0) + float(c.volume)

    return sorted(buckets.values(), key=lambda x: x["ts_ms"])


@router.get("/{account_id}/base-prices")
@limiter.limit("120/minute")
async def get_base_prices(
    request: Request,
    account=Depends(get_owned_account),
    session: AsyncSession = Depends(get_trading_session),
):
    """Get base_price for each combo×symbol pair (for chart price lines)."""
    from app.models.strategy_state import StrategyState
    from app.models.trading_combo import TradingCombo

    combos = (
        await session.execute(
            select(TradingCombo).where(TradingCombo.account_id == account.id, TradingCombo.is_enabled.is_(True))
        )
    ).scalars().all()

    result = []
    for combo in combos:
        symbols = combo.symbols if combo.symbols else [account.symbol]
        for symbol in symbols:
            scope = f"{combo.id}:{symbol}"
            row = (
                await session.execute(
                    select(StrategyState.value).where(
                        StrategyState.account_id == account.id,
                        StrategyState.scope == scope,
                        StrategyState.key == "base_price",
                    )
                )
            ).scalar_one_or_none()
            if row:
                try:
                    bp = float(row)
                    drop_pct = (combo.buy_params or {}).get("drop_pct", 0.01)
                    result.append({
                        "symbol": symbol,
                        "combo_name": combo.name,
                        "base_price": bp,
                        "trigger_price": round(bp * (1 - drop_pct), 2),
                    })
                except (ValueError, TypeError):
                    pass
    return result


@router.get("/{account_id}/asset-status", response_model=AssetStatus)
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

    # P2: 실현손익 (오늘 / 이번 주)
    now_utc = datetime.now(UTC)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now_utc.weekday())  # 월요일 00:00 UTC

    realized_today_stmt = select(
        func.coalesce(func.sum(Lot.net_profit_usdt), 0),
        func.count(),
    ).where(Lot.account_id == account.id, Lot.status == "CLOSED", Lot.sell_time >= today_start)
    r_today = await session.execute(realized_today_stmt)
    pnl_today, closed_today = r_today.one()

    realized_week_stmt = select(
        func.coalesce(func.sum(Lot.net_profit_usdt), 0),
        func.count(),
    ).where(Lot.account_id == account.id, Lot.status == "CLOSED", Lot.sell_time >= week_start)
    r_week = await session.execute(realized_week_stmt)
    pnl_week, closed_week = r_week.one()

    # P2: 심볼별 오픈 lot 수 + 최초 매수 시간
    open_lots_stmt = (
        select(Lot.symbol, func.count().label("cnt"), func.min(Lot.buy_time).label("oldest"))
        .where(Lot.account_id == account.id, Lot.status == "OPEN")
        .group_by(Lot.symbol)
        .order_by(func.count().desc())
    )
    open_lots_result = await session.execute(open_lots_stmt)
    open_lots_by_symbol = []
    for row in open_lots_result.all():
        oldest_dt = row.oldest
        hours = (now_utc - oldest_dt).total_seconds() / 3600 if oldest_dt else 0
        open_lots_by_symbol.append(
            OpenLotSymbol(
                symbol=row.symbol,
                count=row.cnt,
                oldest_buy_time=oldest_dt.isoformat() if oldest_dt else "",
                holding_hours=round(hours, 1),
            )
        )

    # Free USDT balance from exchange
    free_balance = 0.0
    try:
        trader = engine._traders.get(account.id)
        if trader and trader._client:
            free_balance = await trader._client.get_free_balance(account.quote_asset)
    except Exception:
        pass

    return AssetStatus(
        btc_balance=base_qty,
        usdt_balance=round(base_qty * primary_price, 2) if primary_price > 0 else 0,
        free_balance_usdt=round(free_balance, 2),
        held_symbols=held_symbols,
        reserve_pool_qty=reserve_qty,
        reserve_pool_usdt=reserve_cost,
        reserve_pool_pct=reserve_pct,
        pending_earnings_usdt=pending_earnings,
        total_invested_usdt=round(total_invested, 2),
        realized_pnl_today=round(float(pnl_today), 2),
        realized_pnl_week=round(float(pnl_week), 2),
        closed_lots_today=closed_today,
        closed_lots_week=closed_week,
        open_lots_by_symbol=open_lots_by_symbol,
    )


@router.get("/{account_id}/trade-events")
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
