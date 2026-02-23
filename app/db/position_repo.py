from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.position import Position
from app.models.fill import Fill


class PositionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, account_id: UUID, symbol: str) -> Position | None:
        """Fetch position by composite PK."""
        return await self._session.get(Position, (account_id, symbol))

    async def upsert(
        self,
        account_id: UUID,
        symbol: str,
        qty: float,
        cost_basis_usdt: float,
        avg_entry: float,
    ) -> None:
        """PostgreSQL upsert of position row."""
        stmt = (
            pg_insert(Position)
            .values(
                account_id=account_id,
                symbol=symbol,
                qty=qty,
                cost_basis_usdt=cost_basis_usdt,
                avg_entry=avg_entry,
            )
            .on_conflict_do_update(
                index_elements=["account_id", "symbol"],
                set_=dict(
                    qty=qty,
                    cost_basis_usdt=cost_basis_usdt,
                    avg_entry=avg_entry,
                ),
            )
        )
        await self._session.execute(stmt)

    async def recompute_from_fills(
        self,
        account_id: UUID,
        symbol: str,
        session: AsyncSession | None = None,
    ) -> None:
        """Recompute position from all fills for this account/symbol.

        Sums BUY fills (qty, cost) and subtracts SELL fills to derive
        current qty, cost_basis_usdt, and avg_entry.
        """
        sess = session if session is not None else self._session

        stmt = select(Fill).where(
            Fill.account_id == account_id,
            Fill.symbol == symbol,
        )
        result = await sess.execute(stmt)
        fills = result.scalars().all()

        total_qty: float = 0.0
        total_cost: float = 0.0

        for fill in fills:
            qty = float(fill.qty or 0)
            cost = float(fill.quote_qty or 0)
            if fill.side == "BUY":
                total_qty += qty
                total_cost += cost
            elif fill.side == "SELL":
                total_qty -= qty
                total_cost -= cost

        total_qty = max(total_qty, 0.0)
        total_cost = max(total_cost, 0.0)
        avg_entry = (total_cost / total_qty) if total_qty > 0 else 0.0

        upsert_stmt = (
            pg_insert(Position)
            .values(
                account_id=account_id,
                symbol=symbol,
                qty=total_qty,
                cost_basis_usdt=total_cost,
                avg_entry=avg_entry,
            )
            .on_conflict_do_update(
                index_elements=["account_id", "symbol"],
                set_=dict(
                    qty=total_qty,
                    cost_basis_usdt=total_cost,
                    avg_entry=avg_entry,
                ),
            )
        )
        await sess.execute(upsert_stmt)
