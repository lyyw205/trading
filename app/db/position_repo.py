from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fill import Fill
from app.models.position import Position


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

        Uses SQL aggregation to sum BUY/SELL fills and derive
        current qty, cost_basis_usdt, and avg_entry.
        """
        sess = session if session is not None else self._session

        stmt = select(
            func.coalesce(
                func.sum(case((Fill.side == "BUY", Fill.qty), else_=0.0)),
                0.0,
            ),
            func.coalesce(
                func.sum(case((Fill.side == "BUY", Fill.quote_qty), else_=0.0)),
                0.0,
            ),
            func.coalesce(
                func.sum(case((Fill.side == "SELL", Fill.qty), else_=0.0)),
                0.0,
            ),
            func.coalesce(
                func.sum(case((Fill.side == "SELL", Fill.quote_qty), else_=0.0)),
                0.0,
            ),
        ).where(Fill.account_id == account_id, Fill.symbol == symbol)

        row = (await sess.execute(stmt)).one()
        buy_qty, buy_cost, sell_qty, sell_cost = (float(v) for v in row)
        total_qty = max(buy_qty - sell_qty, 0.0)
        total_cost = max(buy_cost - sell_cost, 0.0)
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
