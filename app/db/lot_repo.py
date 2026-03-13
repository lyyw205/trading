from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.models.lot import Lot

logger = logging.getLogger(__name__)


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
            .options(defer(Lot.metadata_))
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

    async def get_open_lots_by_combo(
        self,
        account_id: UUID,
        symbol: str,
        combo_id: UUID,
    ) -> list[Lot]:
        """특정 조합의 미결 로트 조회."""
        stmt = (
            select(Lot)
            .options(defer(Lot.metadata_))
            .where(
                Lot.account_id == account_id,
                Lot.symbol == symbol,
                Lot.combo_id == combo_id,
                Lot.status == "OPEN",
            )
            .order_by(Lot.buy_time_ms.asc())
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
        combo_id: UUID | None = None,
    ) -> Lot:
        """Insert a new lot. Returns existing OPEN lot if buy_order_id is duplicated."""
        if buy_order_id is not None:
            stmt = select(Lot).where(
                Lot.account_id == account_id,
                Lot.buy_order_id == buy_order_id,
                Lot.status == "OPEN",
                Lot.sell_order_id.is_(None),
            )
            result = await self._session.execute(stmt)
            existing = result.scalars().first()
            if existing is not None:
                logger.warning(
                    "insert_lot: 중복 buy_order_id %s 감지, 기존 lot %s 반환",
                    buy_order_id,
                    existing.lot_id,
                )
                return existing

        lot = Lot(
            account_id=account_id,
            symbol=symbol,
            strategy_name=strategy_name,
            buy_order_id=buy_order_id,
            buy_price=buy_price,
            buy_qty=buy_qty,
            buy_time_ms=buy_time_ms,
            combo_id=combo_id,
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
                sell_time=datetime.now(UTC),
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

    async def flush(self) -> None:
        """Flush pending changes to the database within the current transaction."""
        await self._session.flush()

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
