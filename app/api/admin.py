from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func as sa_func
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.account_repo import AccountRepository
from app.db.session import engine_trading, get_trading_session
from app.dependencies import limiter, require_admin
from app.models.account import TradingAccount
from app.models.core_btc_history import CoreBtcHistory
from app.models.fill import Fill
from app.models.lot import Lot
from app.models.order import Order
from app.models.position import Position
from app.models.trading_combo import TradingCombo
from app.models.user import UserProfile
from app.schemas.account import AccountResponse
from app.schemas.trade import OrderResponse
from app.utils.logging import audit_log

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/accounts")
async def admin_list_accounts(request: Request, admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)):
    repo = AccountRepository(session)
    accounts = await repo.get_active_accounts()
    engine = request.app.state.trading_engine
    health = engine.get_account_health()
    return [{
        **AccountResponse.model_validate(a).model_dump(),
        "health": health.get(str(a.id), {}),
    } for a in accounts]


@router.get("/users")
async def admin_list_users(admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)):
    account_count_sq = (
        select(TradingAccount.owner_id, sa_func.count().label("account_count"))
        .group_by(TradingAccount.owner_id)
        .subquery()
    )
    stmt = (
        select(UserProfile, sa_func.coalesce(account_count_sq.c.account_count, 0).label("account_count"))
        .outerjoin(account_count_sq, UserProfile.id == account_count_sq.c.owner_id)
        .order_by(UserProfile.created_at)
    )
    result = await session.execute(stmt)
    rows = result.all()
    return [{"id": str(u.id), "email": u.email, "role": u.role, "is_active": u.is_active, "created_at": str(u.created_at), "account_count": cnt} for u, cnt in rows]


@router.get("/overview")
async def admin_overview(request: Request, admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)):
    engine = request.app.state.trading_engine
    stmt_users = select(UserProfile)
    result_users = await session.execute(stmt_users)
    total_users = len(result_users.scalars().all())
    repo = AccountRepository(session)
    all_accounts = await repo.get_active_accounts()
    return {
        "total_users": total_users,
        "total_accounts": len(all_accounts),
        "active_traders": engine.active_account_count,
        "account_health": engine.get_account_health(),
    }


@router.put("/users/{user_id}/role")
async def admin_set_role(user_id: str, request: Request, admin: dict = Depends(require_admin), session: AsyncSession = Depends(get_trading_session)):
    body = await request.json()
    role = body.get("role", "user")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Invalid role")
    stmt = select(UserProfile).where(UserProfile.id == UUID(user_id))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = role
    await session.commit()

    audit_log("admin_role_changed", user_id=admin["id"], target_user=user_id, new_role=role)
    return {"status": "updated", "user_id": user_id, "role": role}


@router.post("/users")
@limiter.limit("10/minute")
async def admin_create_user(request: Request, admin: dict = Depends(require_admin)):
    """관리자: 새 사용자 생성"""
    from app.schemas.auth import CreateUserRequest
    body = await request.json()
    try:
        req = CreateUserRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    auth_service = request.app.state.auth_service
    try:
        new_user = await auth_service.create_user(req.email, req.password, req.role)
    except ValueError as e:
        if "이미 등록된" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    audit_log("admin_user_created", user_id=admin["id"], target_email=req.email, target_role=req.role)
    return {"status": "created", **new_user}


@router.post("/users/{user_id}/reset-password")
@limiter.limit("10/minute")
async def admin_reset_password(user_id: str, request: Request, admin: dict = Depends(require_admin)):
    """관리자: 사용자 비밀번호 초기화"""
    from app.schemas.auth import ResetPasswordRequest
    body = await request.json()
    try:
        req = ResetPasswordRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    auth_service = request.app.state.auth_service
    try:
        success = await auth_service.reset_password(user_id, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    audit_log("admin_password_reset", user_id=admin["id"], target_user=user_id)
    return {"status": "password_reset"}


@router.put("/users/{user_id}/active")
@limiter.limit("10/minute")
async def admin_set_active(user_id: str, request: Request, admin: dict = Depends(require_admin)):
    """관리자: 계정 활성/비활성화"""
    from app.schemas.auth import SetActiveRequest
    body = await request.json()
    try:
        req = SetActiveRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    auth_service = request.app.state.auth_service
    success = await auth_service.set_user_active(user_id, req.is_active)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    audit_log("admin_user_active_changed", user_id=admin["id"], target_user=user_id, is_active=req.is_active)
    return {"status": "updated", "is_active": req.is_active}


@router.get("/performance")
async def admin_performance(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Aggregate KPIs across all accounts."""
    # All accounts
    repo = AccountRepository(session)
    all_accounts = await repo.get_active_accounts()
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


@router.get("/trades")
async def admin_list_trades(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    account_id: str | None = Query(default=None),
    side: str | None = Query(default=None),
):
    """Cross-account trade history with pagination."""
    stmt = select(Order).order_by(Order.update_time_ms.desc())
    count_stmt = select(sa_func.count(Order.order_id))

    if account_id:
        try:
            uid = UUID(account_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid account_id")
        stmt = stmt.where(Order.account_id == uid)
        count_stmt = count_stmt.where(Order.account_id == uid)
    if side:
        stmt = stmt.where(Order.side == side.upper())
        count_stmt = count_stmt.where(Order.side == side.upper())

    # Total count
    total_result = await session.execute(count_stmt)
    total = total_result.scalar() or 0

    # Paginated results
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    orders = result.scalars().all()

    return {
        "trades": [OrderResponse.model_validate(o).model_dump() for o in orders],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ============================================================
#  Lots — cross-account
# ============================================================
@router.get("/lots")
async def admin_list_lots(
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    status: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    strategy: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Cross-account lot listing with filtering and pagination."""
    stmt = (
        select(Lot, TradingAccount.name.label("account_name"))
        .join(TradingAccount, Lot.account_id == TradingAccount.id)
        .order_by(Lot.buy_time.desc())
    )
    count_stmt = select(sa_func.count(Lot.lot_id))

    if status:
        stmt = stmt.where(Lot.status == status.upper())
        count_stmt = count_stmt.where(Lot.status == status.upper())
    if account_id:
        uid = UUID(account_id)
        stmt = stmt.where(Lot.account_id == uid)
        count_stmt = count_stmt.where(Lot.account_id == uid)
    if strategy:
        stmt = stmt.where(Lot.strategy_name == strategy)
        count_stmt = count_stmt.where(Lot.strategy_name == strategy)

    total = (await session.execute(count_stmt)).scalar() or 0
    result = await session.execute(stmt.offset(offset).limit(limit))
    rows = result.all()

    return {
        "lots": [{
            "lot_id": lot.lot_id,
            "account_id": str(lot.account_id),
            "account_name": account_name,
            "symbol": lot.symbol,
            "strategy_name": lot.strategy_name,
            "buy_price": float(lot.buy_price),
            "buy_qty": float(lot.buy_qty),
            "invested_usdt": round(float(lot.buy_price) * float(lot.buy_qty), 2),
            "buy_time": str(lot.buy_time),
            "status": lot.status,
            "sell_price": float(lot.sell_price) if lot.sell_price else None,
            "sell_time": str(lot.sell_time) if lot.sell_time else None,
            "fee_usdt": float(lot.fee_usdt) if lot.fee_usdt else None,
            "net_profit_usdt": float(lot.net_profit_usdt) if lot.net_profit_usdt else None,
            "combo_id": str(lot.combo_id) if lot.combo_id else None,
        } for lot, account_name in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ============================================================
#  Strategy Catalog
# ============================================================
@router.get("/strategies")
async def admin_list_strategies(
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """List all registered buy/sell strategies with adoption counts."""
    from app.strategies.registry import BuyLogicRegistry, SellLogicRegistry

    # Count combos per buy/sell logic name
    buy_counts_stmt = (
        select(TradingCombo.buy_logic_name, sa_func.count().label("cnt"))
        .group_by(TradingCombo.buy_logic_name)
    )
    sell_counts_stmt = (
        select(TradingCombo.sell_logic_name, sa_func.count().label("cnt"))
        .group_by(TradingCombo.sell_logic_name)
    )
    buy_result = await session.execute(buy_counts_stmt)
    sell_result = await session.execute(sell_counts_stmt)
    buy_counts = {row[0]: row[1] for row in buy_result.all()}
    sell_counts = {row[0]: row[1] for row in sell_result.all()}

    buy_strategies = [
        {**s, "category": "buy", "adoption_count": buy_counts.get(s["name"], 0)}
        for s in BuyLogicRegistry.list_all()
    ]
    sell_strategies = [
        {**s, "category": "sell", "adoption_count": sell_counts.get(s["name"], 0)}
        for s in SellLogicRegistry.list_all()
    ]
    return {"buy": buy_strategies, "sell": sell_strategies}


# ============================================================
#  Combos / Strategies — cross-account
# ============================================================
@router.get("/combos")
async def admin_list_combos(
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    account_id: str | None = Query(default=None),
    enabled: str | None = Query(default=None),
):
    """Cross-account combo overview with open lot counts."""
    open_lots_sq = (
        select(
            Lot.combo_id,
            Lot.account_id,
            sa_func.count(Lot.lot_id).label("open_lots"),
            sa_func.coalesce(sa_func.sum(Lot.buy_price * Lot.buy_qty), 0).label("total_invested"),
        )
        .where(Lot.status == "OPEN")
        .group_by(Lot.combo_id, Lot.account_id)
        .subquery()
    )

    stmt = (
        select(
            TradingCombo,
            TradingAccount.name.label("account_name"),
            sa_func.coalesce(open_lots_sq.c.open_lots, 0).label("open_lots"),
            sa_func.coalesce(open_lots_sq.c.total_invested, 0).label("total_invested"),
        )
        .join(TradingAccount, TradingCombo.account_id == TradingAccount.id)
        .outerjoin(
            open_lots_sq,
            (TradingCombo.id == open_lots_sq.c.combo_id)
            & (TradingCombo.account_id == open_lots_sq.c.account_id),
        )
        .order_by(TradingAccount.name, TradingCombo.name)
    )

    if account_id:
        stmt = stmt.where(TradingCombo.account_id == UUID(account_id))
    if enabled == "true":
        stmt = stmt.where(TradingCombo.is_enabled.is_(True))
    elif enabled == "false":
        stmt = stmt.where(TradingCombo.is_enabled.is_(False))

    result = await session.execute(stmt)
    rows = result.all()

    return [{
        "id": str(combo.id),
        "account_id": str(combo.account_id),
        "account_name": account_name,
        "name": combo.name,
        "buy_logic_name": combo.buy_logic_name,
        "sell_logic_name": combo.sell_logic_name,
        "is_enabled": combo.is_enabled,
        "open_lots": int(open_lots),
        "total_invested": round(float(total_invested), 2),
        "created_at": str(combo.created_at),
        "updated_at": str(combo.updated_at),
    } for combo, account_name, open_lots, total_invested in rows]


# ============================================================
#  Positions — cross-account
# ============================================================
@router.get("/positions")
async def admin_list_positions(
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    account_id: str | None = Query(default=None),
):
    """Cross-account positions."""
    stmt = (
        select(Position, TradingAccount.name.label("account_name"))
        .join(TradingAccount, Position.account_id == TradingAccount.id)
        .order_by(TradingAccount.name, Position.symbol)
    )
    if account_id:
        stmt = stmt.where(Position.account_id == UUID(account_id))

    result = await session.execute(stmt)
    rows = result.all()

    return [{
        "account_id": str(pos.account_id),
        "account_name": account_name,
        "symbol": pos.symbol,
        "qty": float(pos.qty),
        "cost_basis_usdt": round(float(pos.cost_basis_usdt), 2),
        "avg_entry": float(pos.avg_entry),
        "updated_at": str(pos.updated_at),
    } for pos, account_name in rows]


# ============================================================
#  Earnings / Reserve history — cross-account
# ============================================================
@router.get("/earnings")
async def admin_list_earnings(
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    account_id: str | None = Query(default=None),
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
        uid = UUID(account_id)
        hist_stmt = hist_stmt.where(CoreBtcHistory.account_id == uid)
        count_stmt = count_stmt.where(CoreBtcHistory.account_id == uid)

    total = (await session.execute(count_stmt)).scalar() or 0
    result = await session.execute(hist_stmt.offset(offset).limit(limit))
    rows = result.all()

    return {
        "total_pending_usdt": round(total_pending, 2),
        "pending_accounts": [{
            "account_id": str(r[0]),
            "account_name": r[1],
            "pending_usdt": round(float(r[2]), 2),
        } for r in pending_rows],
        "history": [{
            "id": h.id,
            "account_id": str(h.account_id),
            "account_name": account_name,
            "symbol": h.symbol,
            "btc_qty": float(h.btc_qty),
            "cost_usdt": round(float(h.cost_usdt), 2),
            "source": h.source,
            "created_at": str(h.created_at),
        } for h, account_name in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ============================================================
#  Fills — cross-account audit trail
# ============================================================
@router.get("/fills")
async def admin_list_fills(
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    account_id: str | None = Query(default=None),
    side: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Cross-account fill listing for audit."""
    stmt = (
        select(Fill, TradingAccount.name.label("account_name"))
        .join(TradingAccount, Fill.account_id == TradingAccount.id)
        .order_by(Fill.inserted_at.desc())
    )
    count_stmt = select(sa_func.count(Fill.trade_id))

    if account_id:
        uid = UUID(account_id)
        stmt = stmt.where(Fill.account_id == uid)
        count_stmt = count_stmt.where(Fill.account_id == uid)
    if side:
        stmt = stmt.where(Fill.side == side.upper())
        count_stmt = count_stmt.where(Fill.side == side.upper())

    total = (await session.execute(count_stmt)).scalar() or 0
    result = await session.execute(stmt.offset(offset).limit(limit))
    rows = result.all()

    return {
        "fills": [{
            "trade_id": f.trade_id,
            "account_id": str(f.account_id),
            "account_name": account_name,
            "order_id": f.order_id,
            "symbol": f.symbol,
            "side": f.side,
            "price": float(f.price) if f.price else None,
            "qty": float(f.qty) if f.qty else None,
            "quote_qty": float(f.quote_qty) if f.quote_qty else None,
            "commission": float(f.commission) if f.commission else None,
            "commission_asset": f.commission_asset,
            "trade_time_ms": f.trade_time_ms,
            "inserted_at": str(f.inserted_at),
        } for f, account_name in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ============================================================
#  System Health — consolidated monitoring
# ============================================================
@router.get("/system-health")
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
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE state IS NOT NULL AND datname = current_database()"
            )
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
        result = await session.execute(
            text("SELECT count(*) FROM pg_stat_statements WHERE mean_exec_time > 1000")
        )
        slow_queries_count = result.scalar_one()
    except Exception:
        pass

    # --- Dead tuples ---
    dead_tuples = []
    try:
        result = await session.execute(
            text(
                "SELECT relname, n_dead_tup, n_live_tup "
                "FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10"
            )
        )
        dead_tuples = [
            {"table": row[0], "dead_tuples": row[1], "live_tuples": row[2]}
            for row in result.fetchall()
        ]
    except Exception:
        pass

    # --- WebSocket status ---
    engine = request.app.state.trading_engine
    ws = engine._kline_ws
    ws_status = {
        "healthy": ws.is_healthy() if hasattr(ws, "is_healthy") else None,
        "subscriptions": len(getattr(ws, "_subscriptions", {})),
    }

    # --- Trading engine ---
    engine_status = {
        "active_accounts": engine.active_account_count,
        "total_traders": len(engine._traders),
    }

    # --- Candle counts ---
    candle_stats = []
    for table_name in ("price_candles_1m", "price_candles_5m", "price_candles_1h", "price_candles_1d"):
        try:
            result = await session.execute(
                text(f"SELECT symbol, count(*) FROM {table_name} GROUP BY symbol ORDER BY symbol")  # noqa: S608
            )
            for row in result.fetchall():
                candle_stats.append({"table": table_name, "symbol": row[0], "count": row[1]})
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
