from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func as sa_func
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.db.account_repo import AccountRepository
from app.db.session import engine_trading, get_trading_session
from app.dependencies import limiter, require_admin
from app.models.account import TradingAccount
from app.models.core_btc_history import CoreBtcHistory
from app.models.lot import Lot
from app.models.order import Order
from app.models.position import Position
from app.models.user import UserProfile
from app.schemas.account import AccountResponse
from app.services.reconciliation import ReconciliationService

router = APIRouter(prefix="/api/admin", tags=["admin"])

_active_accounts_cache: list | None = None
_active_accounts_ts: float = 0.0
_ACCOUNTS_TTL = 10.0  # seconds


async def _get_active_accounts_cached(session: AsyncSession) -> list:
    global _active_accounts_cache, _active_accounts_ts
    now = time.monotonic()
    if _active_accounts_cache is not None and (now - _active_accounts_ts) < _ACCOUNTS_TTL:
        return _active_accounts_cache
    repo = AccountRepository(session)
    _active_accounts_cache = await repo.get_active_accounts()
    _active_accounts_ts = now
    return _active_accounts_cache


@router.get("/accounts")
@limiter.limit("60/minute")
async def admin_list_accounts(
    request: Request, admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)
):
    accounts = await _get_active_accounts_cached(session)
    engine = request.app.state.trading_engine
    health = engine.get_account_health()
    return [
        {
            **AccountResponse.model_validate(a).model_dump(),
            "health": health.get(str(a.id), {}),
        }
        for a in accounts
    ]


@router.get("/overview")
@limiter.limit("60/minute")
async def admin_overview(
    request: Request, admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)
):
    engine = request.app.state.trading_engine
    total_users_result = await session.execute(select(sa_func.count(UserProfile.id)))
    total_users = total_users_result.scalar() or 0
    all_accounts = await _get_active_accounts_cached(session)
    return {
        "total_users": total_users,
        "total_accounts": len(all_accounts),
        "active_traders": engine.active_account_count,
        "account_health": engine.get_account_health(),
    }


@router.get("/performance")
@limiter.limit("60/minute")
async def admin_performance(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Aggregate KPIs across all accounts."""
    # All accounts
    all_accounts = await _get_active_accounts_cached(session)
    total_accounts = len(all_accounts)
    active_accounts = request.app.state.trading_engine.active_account_count

    # Open lots
    stmt_lots = select(
        sa_func.count(Lot.lot_id),
        sa_func.coalesce(sa_func.sum(Lot.buy_price * Lot.buy_qty), 0),
    ).where(Lot.status == "OPEN")
    lot_result = await session.execute(stmt_lots)
    lot_row = lot_result.one()
    open_lots_count = lot_row[0] or 0
    total_invested_usdt = float(lot_row[1] or 0)

    # 24h trade volume
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    stmt_trades = select(
        sa_func.count(Order.order_id),
        sa_func.coalesce(sa_func.sum(Order.cum_quote_qty), 0),
    ).where(Order.updated_at >= cutoff)
    trade_result = await session.execute(stmt_trades)
    trade_row = trade_result.one()
    trade_count_24h = trade_row[0] or 0
    trade_volume_24h = float(trade_row[1] or 0)

    # Circuit breaker
    cb_tripped = sum(1 for a in all_accounts if a.circuit_breaker_disabled_at is not None)

    # Buy pause
    bp_active = sum(1 for a in all_accounts if a.buy_pause_state == "ACTIVE")
    bp_paused = total_accounts - bp_active

    return {
        "total_accounts": total_accounts,
        "active_accounts": active_accounts,
        "open_lots_count": open_lots_count,
        "total_invested_usdt": round(total_invested_usdt, 2),
        "trade_volume_24h": round(trade_volume_24h, 2),
        "trade_count_24h": trade_count_24h,
        "circuit_breaker_tripped": cb_tripped,
        "circuit_breaker_total": total_accounts,
        "buy_pause_active": bp_active,
        "buy_pause_paused": bp_paused,
    }


# ============================================================
#  Positions — cross-account
# ============================================================
@router.get("/positions")
@limiter.limit("60/minute")
async def admin_list_positions(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    account_id: UUID | None = Query(default=None),
):
    """Cross-account positions."""
    stmt = (
        select(Position, TradingAccount.name.label("account_name"))
        .join(TradingAccount, Position.account_id == TradingAccount.id)
        .order_by(TradingAccount.name, Position.symbol)
    )
    if account_id:
        stmt = stmt.where(Position.account_id == account_id)

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "account_id": str(pos.account_id),
            "account_name": account_name,
            "symbol": pos.symbol,
            "qty": float(pos.qty),
            "cost_basis_usdt": round(float(pos.cost_basis_usdt), 2),
            "avg_entry": float(pos.avg_entry),
            "updated_at": str(pos.updated_at),
        }
        for pos, account_name in rows
    ]


# ============================================================
#  Earnings / Reserve history — cross-account
# ============================================================
@router.get("/earnings")
@limiter.limit("60/minute")
async def admin_list_earnings(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    account_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Cross-account CoreBtcHistory + pending earnings summary."""
    # Pending earnings summary
    pending_stmt = select(
        TradingAccount.id,
        TradingAccount.name,
        TradingAccount.pending_earnings_usdt,
    ).where(TradingAccount.pending_earnings_usdt > 0)
    pending_result = await session.execute(pending_stmt)
    pending_rows = pending_result.all()
    total_pending = sum(float(r[2]) for r in pending_rows)

    # History records
    hist_stmt = (
        select(CoreBtcHistory, TradingAccount.name.label("account_name"))
        .join(TradingAccount, CoreBtcHistory.account_id == TradingAccount.id)
        .order_by(CoreBtcHistory.created_at.desc())
    )
    count_stmt = select(sa_func.count(CoreBtcHistory.id))

    if account_id:
        hist_stmt = hist_stmt.where(CoreBtcHistory.account_id == account_id)
        count_stmt = count_stmt.where(CoreBtcHistory.account_id == account_id)

    total = (await session.execute(count_stmt)).scalar() or 0
    result = await session.execute(hist_stmt.offset(offset).limit(limit))
    rows = result.all()

    return {
        "total_pending_usdt": round(total_pending, 2),
        "pending_accounts": [
            {
                "account_id": str(r[0]),
                "account_name": r[1],
                "pending_usdt": round(float(r[2]), 2),
            }
            for r in pending_rows
        ],
        "history": [
            {
                "id": h.id,
                "account_id": str(h.account_id),
                "account_name": account_name,
                "symbol": h.symbol,
                "btc_qty": float(h.btc_qty),
                "cost_usdt": round(float(h.cost_usdt), 2),
                "source": h.source,
                "created_at": str(h.created_at),
            }
            for h, account_name in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ============================================================
#  System Health — consolidated monitoring
# ============================================================
@router.get("/system-health")
@limiter.limit("30/minute")
async def admin_system_health(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Consolidated system health: DB, WebSocket, candles, engine."""
    import logging

    _logger = logging.getLogger(__name__)

    # --- DB connections ---
    active_connections = None
    try:
        result = await session.execute(
            text("SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL AND datname = current_database()")
        )
        active_connections = result.scalar_one()
    except Exception as exc:
        _logger.warning("Failed to query pg_stat_activity: %s", exc)

    # --- Connection pool ---
    pool = engine_trading.pool
    pool_stats = {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    }

    # --- Slow queries (optional) ---
    slow_queries_count = None
    try:
        result = await session.execute(text("SELECT count(*) FROM pg_stat_statements WHERE mean_exec_time > 1000"))
        slow_queries_count = result.scalar_one()
    except Exception:
        pass

    # --- Dead tuples ---
    dead_tuples = []
    try:
        result = await session.execute(
            text("SELECT relname, n_dead_tup, n_live_tup FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10")
        )
        dead_tuples = [{"table": row[0], "dead_tuples": row[1], "live_tuples": row[2]} for row in result.fetchall()]
    except Exception:
        pass

    # --- WebSocket status ---
    engine = request.app.state.trading_engine
    ws_status = engine.get_ws_status()

    # --- Trading engine ---
    engine_status = {
        "active_accounts": engine.active_account_count,
        "total_traders": engine.active_account_count,
    }

    # --- Candle counts ---
    candle_stats = []
    try:
        union_sql = text(
            "SELECT 'price_candles_1m' AS tbl, symbol, count(*) FROM price_candles_1m GROUP BY symbol "
            "UNION ALL "
            "SELECT 'price_candles_5m', symbol, count(*) FROM price_candles_5m GROUP BY symbol "
            "UNION ALL "
            "SELECT 'price_candles_1h', symbol, count(*) FROM price_candles_1h GROUP BY symbol "
            "UNION ALL "
            "SELECT 'price_candles_1d', symbol, count(*) FROM price_candles_1d GROUP BY symbol "
            "ORDER BY tbl, symbol"
        )
        result = await session.execute(union_sql)
        candle_stats = [{"table": row[0], "symbol": row[1], "count": row[2]} for row in result.fetchall()]
    except Exception:
        pass

    return {
        "database": {
            "active_connections": active_connections,
            "connection_pool": pool_stats,
            "slow_queries_count": slow_queries_count,
            "dead_tuples": dead_tuples,
        },
        "websocket": ws_status,
        "engine": engine_status,
        "candles": candle_stats,
    }


# ============================================================
#  Reconciliation — exchange vs DB consistency
# ============================================================
@router.get("/reconciliation/{account_id}")
@limiter.limit("10/minute")
async def reconcile_account(
    account_id: UUID,
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Manual reconciliation trigger for a specific account."""
    engine = request.app.state.trading_engine
    tc = engine.get_trader_client(account_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Account not running or client not initialized")

    service = ReconciliationService(session, tc[1])
    result = await service.reconcile_account(account_id)
    return result.to_dict()


@router.post("/reconciliation/{account_id}/repair/{symbol}")
@limiter.limit("5/minute")
async def repair_fill_gaps(
    account_id: UUID,
    symbol: str,
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Repair fill gaps for a specific account/symbol."""
    engine = request.app.state.trading_engine
    tc = engine.get_trader_client(account_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Account not running or client not initialized")

    service = ReconciliationService(session, tc[1])
    count = await service.repair_fill_gaps(account_id, symbol.upper())
    await session.commit()
    return {"status": "repaired", "fills_added": count, "symbol": symbol.upper()}


@router.get("/metrics")
async def prometheus_metrics(admin: dict = Depends(require_admin)):
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
