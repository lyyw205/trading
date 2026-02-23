from __future__ import annotations

from uuid import UUID
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lot import Lot


class LotRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_open_lots(
        self,
        account_id: UUID,
        symbol: str,
        strategy_name: str = "lot_stacking",
    ) -> list[Lot]:
        """Get all OPEN lots for an account/symbol/strategy."""
        stmt = (
            select(Lot)
            .where(
                Lot.account_id == account_id,
                Lot.symbol == symbol,
                Lot.strategy_name == strategy_name,
                Lot.status == "OPEN",
            )
            .order_by(Lot.lot_id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def insert_lot(
        self,
        *,
        account_id: UUID,
        symbol: str,
        strategy_name: str,
        buy_order_id: int | None,
        buy_price: float,
        buy_qty: float,
        buy_time_ms: int,
    ) -> Lot:
        """Insert a new lot."""
        lot = Lot(
            account_id=account_id,
            symbol=symbol,
            strategy_name=strategy_name,
            buy_order_id=buy_order_id,
            buy_price=buy_price,
            buy_qty=buy_qty,
            buy_time_ms=buy_time_ms,
            status="OPEN",
        )
        self._session.add(lot)
        await self._session.flush()
        return lot

    async def close_lot(
        self,
        *,
        account_id: UUID,
        lot_id: int,
        sell_price: float,
        sell_time_ms: int,
        fee_usdt: float,
        net_profit_usdt: float,
        sell_order_id: int | None = None,
    ) -> None:
        """Close a lot (set status=CLOSED with sell details)."""
        stmt = (
            update(Lot)
            .where(
                Lot.account_id == account_id,
                Lot.lot_id == lot_id,
            )
            .values(
                status="CLOSED",
                sell_price=sell_price,
                sell_time=datetime.utcnow(),
                sell_time_ms=sell_time_ms,
                fee_usdt=fee_usdt,
                net_profit_usdt=net_profit_usdt,
                sell_order_id=sell_order_id,
            )
        )
        await self._session.execute(stmt)

    async def set_sell_order(
        self,
        *,
        account_id: UUID,
        lot_id: int,
        sell_order_id: int,
        sell_order_time_ms: int,
    ) -> None:
        """Set sell order on an open lot."""
        stmt = (
            update(Lot)
            .where(
                Lot.account_id == account_id,
                Lot.lot_id == lot_id,
            )
            .values(
                sell_order_id=sell_order_id,
                sell_order_time_ms=sell_order_time_ms,
            )
        )
        await self._session.execute(stmt)

    async def clear_sell_order(
        self,
        *,
        account_id: UUID,
        lot_id: int,
    ) -> None:
        """Clear sell order (cancelled/expired)."""
        stmt = (
            update(Lot)
            .where(
                Lot.account_id == account_id,
                Lot.lot_id == lot_id,
            )
            .values(sell_order_id=None, sell_order_time_ms=None)
        )
        await self._session.execute(stmt)
