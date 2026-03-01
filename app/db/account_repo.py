from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import TradingAccount


class AccountRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, account_id: UUID) -> TradingAccount | None:
        """Fetch account by primary key."""
        return await self._session.get(TradingAccount, account_id)

    async def get_active_accounts(self) -> list[TradingAccount]:
        """Return all accounts where is_active=True."""
        stmt = select(TradingAccount).where(TradingAccount.is_active.is_(True))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_owner(self, owner_id: UUID) -> list[TradingAccount]:
        """Return all accounts belonging to a given owner."""
        from sqlalchemy.orm import selectinload
        stmt = (
            select(TradingAccount)
            .where(TradingAccount.owner_id == owner_id)
            .options(selectinload(TradingAccount.trading_combos))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, account: TradingAccount) -> TradingAccount:
        """Persist a new TradingAccount and flush to obtain server defaults."""
        self._session.add(account)
        await self._session.flush()
        return account

    async def update_circuit_breaker(
        self,
        account_id: UUID,
        failures: int,
        disabled_at: datetime | None,
    ) -> None:
        """Update circuit-breaker state (failure count and disabled timestamp)."""
        stmt = (
            update(TradingAccount)
            .where(TradingAccount.id == account_id)
            .values(
                circuit_breaker_failures=failures,
                circuit_breaker_disabled_at=disabled_at,
            )
        )
        await self._session.execute(stmt)

    async def reset_circuit_breaker(self, account_id: UUID) -> None:
        """Clear circuit-breaker: failures=0, disabled_at=None, is_active=True."""
        stmt = (
            update(TradingAccount)
            .where(TradingAccount.id == account_id)
            .values(
                circuit_breaker_failures=0,
                circuit_breaker_disabled_at=None,
                is_active=True,
            )
        )
        await self._session.execute(stmt)

    async def get_all_accounts(self) -> list[TradingAccount]:
        """Return all accounts regardless of active status."""
        stmt = select(TradingAccount)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_accounts_with_owner(self) -> list[TradingAccount]:
        """Return all accounts with owner and trading_combos eagerly loaded."""
        from sqlalchemy.orm import selectinload
        stmt = select(TradingAccount).options(
            selectinload(TradingAccount.owner),
            selectinload(TradingAccount.trading_combos),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_last_success(self, account_id: UUID) -> None:
        """Stamp last_success_at with the current UTC time."""
        stmt = (
            update(TradingAccount)
            .where(TradingAccount.id == account_id)
            .values(last_success_at=datetime.utcnow())
        )
        await self._session.execute(stmt)
