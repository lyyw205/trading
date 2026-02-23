from __future__ import annotations
from typing import Optional
from uuid import UUID
from sqlalchemy import select, delete
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

    async def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """strategy_state에서 (account_id, scope, key)로 값 조회"""
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
        """strategy_state에 (account_id, scope, key) -> value upsert"""
        stmt = pg_insert(StrategyState).values(
            account_id=self.account_id,
            scope=self.scope,
            key=key,
            value=str(value),
        ).on_conflict_do_update(
            index_elements=["account_id", "scope", "key"],
            set_={"value": str(value)},
        )
        await self._session.execute(stmt)

    async def delete(self, key: str) -> None:
        stmt = delete(StrategyState).where(
            StrategyState.account_id == self.account_id,
            StrategyState.scope == self.scope,
            StrategyState.key == key,
        )
        await self._session.execute(stmt)

    async def clear_keys(self, *keys: str) -> None:
        """여러 키를 빈 문자열로 설정 (기존 _clear_pending_buy 패턴)"""
        for key in keys:
            await self.set(key, "")

    async def get_all(self) -> dict[str, str]:
        """이 scope의 모든 키-값 조회"""
        stmt = select(StrategyState.key, StrategyState.value).where(
            StrategyState.account_id == self.account_id,
            StrategyState.scope == self.scope,
        )
        result = await self._session.execute(stmt)
        return {row.key: row.value for row in result}
