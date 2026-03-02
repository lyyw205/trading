from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy_state import StrategyState


class StrategyStateStore:
    """
    전략별 상태를 strategy_state 테이블에서 관리.
    scope별로 격리: 'lot_stacking', 'trend_buy', 'shared'
    """

    def __init__(self, account_id: UUID, scope: str, session: AsyncSession):
        self.account_id = account_id
        self.scope = scope
        self._session = session
        self._cache: dict[str, str] | None = None

    @property
    def session(self) -> AsyncSession:
        """Public access for ORM entity operations."""
        return self._session

    def with_scope(self, scope: str) -> StrategyStateStore:
        """Create a store with the same session but different scope (cache not inherited)."""
        return StrategyStateStore(self.account_id, scope, self._session)

    async def preload(self) -> None:
        """Bulk-load all keys for this scope into the in-memory cache."""
        self._cache = await self.get_all()

    async def get(self, key: str, default: str | None = None) -> str | None:
        """strategy_state에서 (account_id, scope, key)로 값 조회"""
        if self._cache is not None:
            val = self._cache.get(key)
            return val if val is not None else default
        stmt = select(StrategyState.value).where(
            StrategyState.account_id == self.account_id,
            StrategyState.scope == self.scope,
            StrategyState.key == key,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return row if row is not None else default

    async def get_float(self, key: str, default: float = 0.0) -> float:
        raw = await self.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            return float(raw)
        except Exception:
            return default

    async def get_int(self, key: str, default: int = 0) -> int:
        raw = await self.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            return int(float(raw))
        except Exception:
            return default

    async def set(self, key: str, value) -> None:
        """strategy_state에 (account_id, scope, key) -> value upsert (write-through cache)"""
        str_value = str(value)
        stmt = pg_insert(StrategyState).values(
            account_id=self.account_id,
            scope=self.scope,
            key=key,
            value=str_value,
        ).on_conflict_do_update(
            index_elements=["account_id", "scope", "key"],
            set_={"value": str_value},
        )
        await self._session.execute(stmt)
        if self._cache is not None:
            self._cache[key] = str_value

    async def set_many(self, items: dict[str, object]) -> None:
        """Batch upsert multiple keys in a single statement."""
        if not items:
            return
        rows = [
            {"account_id": self.account_id, "scope": self.scope, "key": k, "value": str(v)}
            for k, v in items.items()
        ]
        stmt = pg_insert(StrategyState).values(rows).on_conflict_do_update(
            index_elements=["account_id", "scope", "key"],
            set_={"value": pg_insert(StrategyState).excluded.value},
        )
        await self._session.execute(stmt)
        if self._cache is not None:
            for k, v in items.items():
                self._cache[k] = str(v)

    async def delete(self, key: str) -> None:
        stmt = delete(StrategyState).where(
            StrategyState.account_id == self.account_id,
            StrategyState.scope == self.scope,
            StrategyState.key == key,
        )
        await self._session.execute(stmt)
        if self._cache is not None:
            self._cache.pop(key, None)

    async def clear_keys(self, *keys: str) -> None:
        """여러 키 삭제 — single batch DELETE (pending 상태 정리 등)"""
        if not keys:
            return
        stmt = delete(StrategyState).where(
            StrategyState.account_id == self.account_id,
            StrategyState.scope == self.scope,
            StrategyState.key.in_(keys),
        )
        await self._session.execute(stmt)
        if self._cache is not None:
            for key in keys:
                self._cache.pop(key, None)

    async def get_all(self) -> dict[str, str]:
        """이 scope의 모든 키-값 조회"""
        stmt = select(StrategyState.key, StrategyState.value).where(
            StrategyState.account_id == self.account_id,
            StrategyState.scope == self.scope,
        )
        result = await self._session.execute(stmt)
        return {row.key: row.value for row in result}
