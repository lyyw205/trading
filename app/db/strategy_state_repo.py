from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy_state import StrategyState
from app.strategies.state_store import StrategyStateStore  # re-export

__all__ = ["StrategyStateStore", "get_all_for_account"]


async def get_all_for_account(
    account_id: UUID,
    session: AsyncSession,
) -> dict[str, dict[str, str]]:
    """Get all strategy state for an account, grouped by scope."""
    stmt = select(StrategyState).where(StrategyState.account_id == account_id)
    result = await session.execute(stmt)
    rows = result.scalars().all()

    grouped: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.scope not in grouped:
            grouped[row.scope] = {}
        grouped[row.scope][row.key] = row.value or ""
    return grouped
