from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Request, HTTPException, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_trading_session
from app.db.lot_repo import LotRepository
from app.db.position_repo import PositionRepository
from app.db.account_repo import AccountRepository
from app.db.price_repo import get_candles, get_snapshots
from app.db.strategy_state_repo import get_all_for_account
from app.models.lot import Lot
from app.models.order import Order
from app.services.account_state_manager import AccountStateManager
from app.schemas.dashboard import DashboardSummary, TuneUpdate
from app.schemas.trade import LotResponse, OrderResponse

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _check_account_access(account, request: Request):
    """Verify user owns the account or is admin."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if str(account.owner_id) != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("/{account_id}", response_model=DashboardSummary)
async def get_dashboard(account_id: UUID, request: Request, session: AsyncSession = Depends(get_trading_session)):
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    _check_account_access(account, request)

    # Position
    pos_repo = PositionRepository(session)
    position = await pos_repo.get(account_id, account.symbol)

    # Open lots count
    lot_repo = LotRepository(session)
    open_lots = await lot_repo.get_open_lots(account_id, account.symbol, "lot_stacking")
    open_trend_lots = await lot_repo.get_open_lots(account_id, account.symbol, "trend_buy")

    # Total net profit from closed lots
    stmt = select(func.coalesce(func.sum(Lot.net_profit_usdt), 0)).where(
        Lot.account_id == account_id, Lot.status == "CLOSED"
    )
    result = await session.execute(stmt)
    total_profit = float(result.scalar_one())

    # Reserve from shared state
    account_state = AccountStateManager(account_id, session)
    reserve_qty = await account_state.get_reserve_qty()
    reserve_cost = await account_state.get_reserve_cost_usdt()

    # Health
    engine = request.app.state.trading_engine
    health = engine.get_account_health().get(str(account_id), {})

    # Current price
    price_collector = engine._price_collector
    cur_price = await price_collector.get_price(account.symbol)

    return DashboardSummary(
        account_id=str(account_id),
        account_name=account.name,
        symbol=account.symbol,
        current_price=cur_price,
        position={"qty": float(position.qty), "cost_basis_usdt": float(position.cost_basis_usdt), "avg_entry": float(position.avg_entry)} if position else None,
        open_lots_count=len(open_lots) + len(open_trend_lots),
        total_net_profit=total_profit,
        reserve_qty=reserve_qty,
        reserve_cost_usdt=reserve_cost,
        is_active=account.is_active,
        health=health,
    )


@router.get("/{account_id}/lots", response_model=list[LotResponse])
async def get_lots(account_id: UUID, request: Request, strategy: str = "lot_stacking", status: str = "OPEN", session: AsyncSession = Depends(get_trading_session)):
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    _check_account_access(account, request)
    stmt = select(Lot).where(Lot.account_id == account_id, Lot.strategy_name == strategy, Lot.status == status).order_by(Lot.lot_id.desc()).limit(100)
    result = await session.execute(stmt)
    return [LotResponse.model_validate(l) for l in result.scalars().all()]


@router.get("/{account_id}/trades", response_model=list[OrderResponse])
async def get_trades(account_id: UUID, request: Request, limit: int = Query(default=50, le=200), session: AsyncSession = Depends(get_trading_session)):
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    _check_account_access(account, request)
    stmt = select(Order).where(Order.account_id == account_id).order_by(Order.update_time_ms.desc()).limit(limit)
    result = await session.execute(stmt)
    return [OrderResponse.model_validate(o) for o in result.scalars().all()]


@router.get("/{account_id}/price_candles")
async def get_price_candles(account_id: UUID, request: Request, from_ms: int = 0, to_ms: int = 0, session: AsyncSession = Depends(get_trading_session)):
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404)
    _check_account_access(account, request)
    candles = await get_candles(account.symbol, from_ms, to_ms, session)
    return [{"ts_ms": c.ts_ms, "open": float(c.open), "high": float(c.high), "low": float(c.low), "close": float(c.close)} for c in candles]


@router.get("/{account_id}/tune")
async def get_tune(account_id: UUID, request: Request, session: AsyncSession = Depends(get_trading_session)):
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    _check_account_access(account, request)
    from app.models.strategy_config import StrategyConfig
    stmt = select(StrategyConfig).where(StrategyConfig.account_id == account_id)
    result = await session.execute(stmt)
    configs = list(result.scalars().all())
    return [{"strategy_name": c.strategy_name, "params": c.params or {}} for c in configs]


@router.post("/{account_id}/tune")
async def update_tune(account_id: UUID, request: Request, body: TuneUpdate, session: AsyncSession = Depends(get_trading_session)):
    repo = AccountRepository(session)
    account = await repo.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    _check_account_access(account, request)
    from app.models.strategy_config import StrategyConfig
    stmt = select(StrategyConfig).where(
        StrategyConfig.account_id == account_id, StrategyConfig.strategy_name == body.strategy_name
    )
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()
    if config:
        config.params = {**(config.params or {}), **body.params}
    else:
        config = StrategyConfig(account_id=account_id, strategy_name=body.strategy_name, params=body.params, is_enabled=True)
        session.add(config)
    await session.commit()
    return {"status": "updated", "params": config.params}
