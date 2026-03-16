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
    request: Request, _admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)
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
    request: Request, _admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)
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
    _admin: dict = Depends(require_admin),
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
    circuit_breaker_tripped_count = sum(1 for a in all_accounts if a.circuit_breaker_disabled_at is not None)

    # Buy pause
    buy_pause_active_count = sum(1 for a in all_accounts if a.buy_pause_state == "ACTIVE")
    bp_paused = total_accounts - buy_pause_active_count

    return {
        "total_accounts": total_accounts,
        "active_accounts": active_accounts,
        "open_lots_count": open_lots_count,
        "total_invested_usdt": round(total_invested_usdt, 2),
        "trade_volume_24h": round(trade_volume_24h, 2),
        "trade_count_24h": trade_count_24h,
        "circuit_breaker_tripped": circuit_breaker_tripped_count,
        "circuit_breaker_total": total_accounts,
        "buy_pause_active": buy_pause_active_count,
        "buy_pause_paused": bp_paused,
    }


# ============================================================
#  Positions — cross-account
# ============================================================
@router.get("/positions")
@limiter.limit("60/minute")
async def admin_list_positions(
    request: Request,
    _admin: dict = Depends(require_admin),
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
    _admin: dict = Depends(require_admin),
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
    total_pending = sum(float(pending_row[2]) for pending_row in pending_rows)

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
                "account_id": str(pending_row[0]),
                "account_name": pending_row[1],
                "pending_usdt": round(float(pending_row[2]), 2),
            }
            for pending_row in pending_rows
        ],
        "history": [
            {
                "id": history_entry.id,
                "account_id": str(history_entry.account_id),
                "account_name": account_name,
                "symbol": history_entry.symbol,
                "btc_qty": float(history_entry.btc_qty),
                "cost_usdt": round(float(history_entry.cost_usdt), 2),
                "source": history_entry.source,
                "created_at": str(history_entry.created_at),
            }
            for history_entry, account_name in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ============================================================
#  System Health — consolidated monitoring
# ============================================================

# Alert thresholds and cooldown tracking
_ALERT_THRESHOLDS = {
    "cpu_percent": 90.0,
    "memory_percent": 90.0,
    "db_pool_exhaustion": 0.9,  # checked_out / pool_size ratio
    "api_error_rate": 0.1,  # 10%
}
_alert_cooldowns: dict[str, float] = {}
_ALERT_COOLDOWN_SEC = 300  # 5 minutes


def _check_alerts(server: dict, db_pool: dict, api_overview: dict) -> list[dict]:
    """Check metrics against thresholds, return active alerts."""
    now = time.time()
    alerts = []

    checks = [
        ("cpu_high", server.get("cpu_percent", 0), _ALERT_THRESHOLDS["cpu_percent"], "CPU"),
        ("memory_high", server.get("memory_percent", 0), _ALERT_THRESHOLDS["memory_percent"], "메모리"),
    ]
    pool_size = db_pool.get("pool_size", 1) or 1
    pool_ratio = db_pool.get("checked_out", 0) / pool_size
    checks.append(("db_pool_exhaustion", pool_ratio, _ALERT_THRESHOLDS["db_pool_exhaustion"], "DB 풀"))

    error_rate = api_overview.get("error_rate", 0)
    checks.append(("api_error_high", error_rate, _ALERT_THRESHOLDS["api_error_rate"], "API 에러율"))

    for alert_type, value, threshold, label in checks:
        if value >= threshold:
            alerts.append(
                {
                    "type": alert_type,
                    "label": label,
                    "value": round(value, 2),
                    "threshold": threshold,
                    "severity": "HIGH",
                }
            )
            # Discord alert with cooldown
            if now - _alert_cooldowns.get(alert_type, 0) >= _ALERT_COOLDOWN_SEC:
                _alert_cooldowns[alert_type] = now
                _send_discord_alert(alert_type, label, value, threshold)

    return alerts


def _send_discord_alert(alert_type: str, label: str, value: float, threshold: float) -> None:
    """Send alert to Discord webhook (fire-and-forget)."""
    import asyncio

    import httpx

    from app.config import get_settings

    webhook_url = get_settings().discord_webhook_url
    if not webhook_url:
        return

    embed = {
        "title": f"⚠️ {label} 임계값 초과",
        "description": f"**{label}**: {value:.1f} (임계값: {threshold:.0f})",
        "color": 0xFF4444,
        "fields": [
            {"name": "유형", "value": alert_type, "inline": True},
            {"name": "현재값", "value": f"{value:.1f}", "inline": True},
            {"name": "임계값", "value": f"{threshold:.0f}", "inline": True},
        ],
    }

    async def _send():
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(webhook_url, json={"embeds": [embed]})
        except Exception:
            pass

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send())
    except RuntimeError:
        pass


def _get_server_stats() -> dict:
    """Get server resource stats via psutil (non-blocking)."""
    try:
        import os

        import psutil

        proc = psutil.Process(os.getpid())
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": round(mem.percent, 1),
            "memory_rss_mb": round(proc.memory_info().rss / 1024 / 1024, 1),
            "disk_usage_percent": round(disk.percent, 1),
            "uptime_hours": round((time.time() - proc.create_time()) / 3600, 1),
            "threads": proc.num_threads(),
        }
    except Exception:
        return {
            "cpu_percent": 0,
            "memory_percent": 0,
            "memory_rss_mb": 0,
            "disk_usage_percent": 0,
            "uptime_hours": 0,
            "threads": 0,
        }


def _get_pool_stats() -> dict:
    pool = engine_trading.pool
    return {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    }


@router.get("/system-health/light")
@limiter.limit("120/minute")
async def admin_system_health_light(
    request: Request,
    _admin: dict = Depends(require_admin),
):
    """Lightweight health endpoint for 5-second polling. Zero DB queries."""
    from app.utils.request_metrics import request_metrics

    server = _get_server_stats()
    db_pool = _get_pool_stats()
    api_overview = request_metrics.get_overview()

    engine = request.app.state.trading_engine
    ws_status = engine.get_ws_status()

    alerts = _check_alerts(server, db_pool, api_overview)

    return {
        "server": server,
        "db_pool": db_pool,
        "api_overview": api_overview,
        "engine": {"active_accounts": engine.active_account_count},
        "websocket": ws_status,
        "alerts": alerts,
        "ts": datetime.now(UTC).isoformat(),
    }


@router.get("/system-health")
@limiter.limit("30/minute")
async def admin_system_health(
    request: Request,
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Full system health for 60-second polling. Includes DB queries."""
    import logging

    from app.utils.request_metrics import request_metrics

    _logger = logging.getLogger(__name__)

    server = _get_server_stats()
    db_pool = _get_pool_stats()
    api_overview = request_metrics.get_overview()
    api_top = request_metrics.get_summary()

    # --- DB connections ---
    active_connections = None
    try:
        conn_result = await session.execute(
            text("SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL AND datname = current_database()")
        )
        active_connections = conn_result.scalar_one()
    except Exception as exc:
        _logger.warning("Failed to query pg_stat_activity: %s", exc)

    # --- Slow queries (optional) ---
    slow_queries = []
    try:
        slow_query_result = await session.execute(
            text(
                "SELECT query, calls, round(mean_exec_time::numeric, 1) as avg_ms, "
                "round(total_exec_time::numeric, 0) as total_ms "
                "FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 5"
            )
        )
        slow_queries = [
            {"query": row[0][:120], "calls": row[1], "avg_ms": float(row[2]), "total_ms": float(row[3])}
            for row in slow_query_result.fetchall()
        ]
    except Exception:
        pass

    # --- Dead tuples ---
    dead_tuples = []
    try:
        dead_tuple_result = await session.execute(
            text("SELECT relname, n_dead_tup, n_live_tup FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10")
        )
        dead_tuples = [
            {"table": row[0], "dead_tuples": row[1], "live_tuples": row[2]} for row in dead_tuple_result.fetchall()
        ]
    except Exception:
        pass

    # --- DB size ---
    db_size_mb = 0
    try:
        db_size_result = await session.execute(text("SELECT pg_database_size(current_database()) / 1024 / 1024"))
        db_size_mb = db_size_result.scalar_one()
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

    # --- Trading activity (last hour, using indexed update_time_ms) ---
    now_ms = int(time.time() * 1000)
    hour_ago_ms = now_ms - 3600_000
    orders_last_hour = 0
    fills_last_hour = 0
    try:
        order_count_result = await session.execute(select(sa_func.count()).where(Order.update_time_ms >= hour_ago_ms))
        orders_last_hour = order_count_result.scalar_one()
        fill_count_result = await session.execute(
            select(sa_func.count()).where(Order.update_time_ms >= hour_ago_ms, Order.status == "FILLED")
        )
        fills_last_hour = fill_count_result.scalar_one()
    except Exception:
        pass

    # --- Error trend (last 6 hours, hourly buckets) ---
    error_trend = []
    try:
        error_trend_result = await session.execute(
            text(
                "SELECT date_trunc('hour', logged_at) AS h, count(*) "
                "FROM persistent_logs "
                "WHERE logged_at >= now() - interval '6 hours' AND level = 'ERROR' "
                "GROUP BY h ORDER BY h"
            )
        )
        error_trend = [{"hour": row[0].isoformat(), "count": row[1]} for row in error_trend_result.fetchall()]
    except Exception:
        pass

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
        candle_result = await session.execute(union_sql)
        candle_stats = [{"table": row[0], "symbol": row[1], "count": row[2]} for row in candle_result.fetchall()]
    except Exception:
        pass

    return {
        "server": server,
        "database": {
            "active_connections": active_connections,
            "connection_pool": db_pool,
            "slow_queries": slow_queries,
            "dead_tuples": dead_tuples,
            "db_size_mb": db_size_mb,
        },
        "api_metrics": {
            "overview": api_overview,
            "top_endpoints": api_top,
        },
        "trading_activity": {
            "orders_last_hour": orders_last_hour,
            "fills_last_hour": fills_last_hour,
            "error_trend": error_trend,
        },
        "websocket": ws_status,
        "engine": engine_status,
        "candles": candle_stats,
        "alerts": _check_alerts(server, db_pool, api_overview),
        "ts": datetime.now(UTC).isoformat(),
    }


# ============================================================
#  Reconciliation — exchange vs DB consistency
# ============================================================
@router.get("/reconciliation/{account_id}")
@limiter.limit("10/minute")
async def reconcile_account(
    account_id: UUID,
    request: Request,
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Manual reconciliation trigger for a specific account."""
    engine = request.app.state.trading_engine
    tc = engine.get_trader_client(account_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Account not running or client not initialized")
    _trader, exchange_client = tc

    service = ReconciliationService(session, exchange_client)
    result = await service.reconcile_account(account_id)
    return result.to_dict()


@router.post("/reconciliation/{account_id}/repair/{symbol}")
@limiter.limit("5/minute")
async def repair_fill_gaps(
    account_id: UUID,
    symbol: str,
    request: Request,
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Repair fill gaps for a specific account/symbol."""
    engine = request.app.state.trading_engine
    tc = engine.get_trader_client(account_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Account not running or client not initialized")
    _trader, exchange_client = tc

    service = ReconciliationService(session, exchange_client)
    count = await service.repair_fill_gaps(account_id, symbol.upper())
    await session.commit()
    return {"status": "repaired", "fills_added": count, "symbol": symbol.upper()}


@router.get("/metrics")
async def prometheus_metrics(_admin: dict = Depends(require_admin)):
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
