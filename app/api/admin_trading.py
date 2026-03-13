from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.db.session import get_trading_session
from app.dependencies import limiter, require_admin
from app.models.account import TradingAccount
from app.models.fill import Fill
from app.models.lot import Lot
from app.models.order import Order
from app.models.trading_combo import TradingCombo
from app.schemas.trade import OrderResponse
from app.strategies.registry import BuyLogicRegistry, SellLogicRegistry

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/trades")
@limiter.limit("60/minute")
async def admin_list_trades(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    account_id: UUID | None = Query(default=None),
    side: Literal["BUY", "SELL"] | None = Query(default=None),
):
    """Cross-account trade history with pagination."""
    stmt = select(Order).options(defer(Order.raw_json)).order_by(Order.update_time_ms.desc())
    count_stmt = select(sa_func.count(Order.order_id))

    if account_id:
        stmt = stmt.where(Order.account_id == account_id)
        count_stmt = count_stmt.where(Order.account_id == account_id)
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
@limiter.limit("60/minute")
async def admin_list_lots(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    status: Literal["OPEN", "CLOSED", "CANCELLED", "MERGED"] | None = Query(default=None),
    account_id: UUID | None = Query(default=None),
    strategy: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Cross-account lot listing with filtering and pagination."""
    stmt = (
        select(Lot, TradingAccount.name.label("account_name"))
        .options(defer(Lot.metadata_))
        .join(TradingAccount, Lot.account_id == TradingAccount.id)
        .order_by(Lot.buy_time.desc())
    )
    count_stmt = select(sa_func.count(Lot.lot_id))

    if status:
        stmt = stmt.where(Lot.status == status.upper())
        count_stmt = count_stmt.where(Lot.status == status.upper())
    if account_id:
        stmt = stmt.where(Lot.account_id == account_id)
        count_stmt = count_stmt.where(Lot.account_id == account_id)
    if strategy:
        stmt = stmt.where(Lot.strategy_name == strategy)
        count_stmt = count_stmt.where(Lot.strategy_name == strategy)

    total = (await session.execute(count_stmt)).scalar() or 0
    result = await session.execute(stmt.offset(offset).limit(limit))
    rows = result.all()

    return {
        "lots": [
            {
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
            }
            for lot, account_name in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ============================================================
#  Strategy Catalog
# ============================================================
@router.get("/strategies")
@limiter.limit("60/minute")
async def admin_list_strategies(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """List all registered buy/sell strategies with adoption counts."""
    # Count combos per buy/sell logic name
    buy_counts_stmt = select(TradingCombo.buy_logic_name, sa_func.count().label("cnt")).group_by(
        TradingCombo.buy_logic_name
    )
    sell_counts_stmt = select(TradingCombo.sell_logic_name, sa_func.count().label("cnt")).group_by(
        TradingCombo.sell_logic_name
    )
    buy_result = await session.execute(buy_counts_stmt)
    sell_result = await session.execute(sell_counts_stmt)
    buy_counts = {row[0]: row[1] for row in buy_result.all()}
    sell_counts = {row[0]: row[1] for row in sell_result.all()}

    buy_strategies = [
        {**s, "category": "buy", "adoption_count": buy_counts.get(s["name"], 0)} for s in BuyLogicRegistry.list_all()
    ]
    sell_strategies = [
        {**s, "category": "sell", "adoption_count": sell_counts.get(s["name"], 0)} for s in SellLogicRegistry.list_all()
    ]
    return {"buy": buy_strategies, "sell": sell_strategies}


# ============================================================
#  Combos / Strategies — cross-account
# ============================================================
@router.get("/combos")
@limiter.limit("60/minute")
async def admin_list_combos(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    account_id: UUID | None = Query(default=None),
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
            (TradingCombo.id == open_lots_sq.c.combo_id) & (TradingCombo.account_id == open_lots_sq.c.account_id),
        )
        .order_by(TradingAccount.name, TradingCombo.name)
    )

    if account_id:
        stmt = stmt.where(TradingCombo.account_id == account_id)
    if enabled == "true":
        stmt = stmt.where(TradingCombo.is_enabled.is_(True))
    elif enabled == "false":
        stmt = stmt.where(TradingCombo.is_enabled.is_(False))

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
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
        }
        for combo, account_name, open_lots, total_invested in rows
    ]


# ============================================================
#  Fills — cross-account audit trail
# ============================================================
@router.get("/fills")
@limiter.limit("60/minute")
async def admin_list_fills(
    request: Request,
    admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    account_id: UUID | None = Query(default=None),
    side: Literal["BUY", "SELL"] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Cross-account fill listing for audit."""
    stmt = (
        select(Fill, TradingAccount.name.label("account_name"))
        .options(defer(Fill.raw_json))
        .join(TradingAccount, Fill.account_id == TradingAccount.id)
        .order_by(Fill.inserted_at.desc())
    )
    count_stmt = select(sa_func.count(Fill.trade_id))

    if account_id:
        stmt = stmt.where(Fill.account_id == account_id)
        count_stmt = count_stmt.where(Fill.account_id == account_id)
    if side:
        stmt = stmt.where(Fill.side == side.upper())
        count_stmt = count_stmt.where(Fill.side == side.upper())

    total = (await session.execute(count_stmt)).scalar() or 0
    result = await session.execute(stmt.offset(offset).limit(limit))
    rows = result.all()

    return {
        "fills": [
            {
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
            }
            for f, account_name in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
