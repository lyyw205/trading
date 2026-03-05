from __future__ import annotations

import asyncio
from typing import ClassVar
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import TradingAccount
from app.strategies.state_store import StrategyStateStore


class AccountStateManager:
    """
    계정 레벨 공유 상태 관리.
    reserve pool(reserve_qty, reserve_cost_usdt)은 LOT/TREND 양쪽에서 접근.
    pending_earnings_usdt는 trading_accounts 정식 컬럼으로 원자적 접근.

    reserve 변경은 per-account asyncio lock으로 read-modify-write 경합 방지.
    (단일 프로세스 asyncio 앱 전제. 다중 프로세스 확장 시 DB-level lock 필요.)
    """

    _reserve_locks: ClassVar[dict[UUID, asyncio.Lock]] = {}

    def __init__(self, account_id: UUID, session: AsyncSession):
        self._account_id = account_id
        self._session = session
        self._store = StrategyStateStore(account_id, scope="shared", session=session)

    async def preload(self) -> None:
        """Bulk-load the shared scope into cache to avoid per-key DB queries."""
        await self._store.preload()

    @classmethod
    def _get_reserve_lock(cls, account_id: UUID) -> asyncio.Lock:
        if account_id not in cls._reserve_locks:
            cls._reserve_locks[account_id] = asyncio.Lock()
        return cls._reserve_locks[account_id]

    @classmethod
    def remove_lock(cls, account_id: UUID) -> None:
        """Remove lock for a deleted/deactivated account to prevent memory leak."""
        cls._reserve_locks.pop(account_id, None)

    # ---- reserve (shared scope KV, guarded by asyncio lock) ----

    async def get_reserve_qty(self) -> float:
        return await self._store.get_float("reserve_qty", 0.0)

    async def set_reserve_qty(self, qty: float) -> None:
        await self._store.set("reserve_qty", float(qty))

    async def add_reserve_qty(self, delta: float) -> float:
        async with self._get_reserve_lock(self._account_id):
            current = await self.get_reserve_qty()
            new_val = current + delta
            await self.set_reserve_qty(new_val)
            return new_val

    async def get_reserve_cost_usdt(self) -> float:
        return await self._store.get_float("reserve_cost_usdt", 0.0)

    async def set_reserve_cost_usdt(self, cost: float) -> None:
        await self._store.set("reserve_cost_usdt", float(cost))

    async def add_reserve_cost_usdt(self, delta: float) -> float:
        async with self._get_reserve_lock(self._account_id):
            current = await self.get_reserve_cost_usdt()
            new_val = current + delta
            await self.set_reserve_cost_usdt(new_val)
            return new_val

    # ---- pending_earnings (신규 - 원자적 SQL) ----

    async def get_pending_earnings(self) -> float:
        """trading_accounts.pending_earnings_usdt 조회"""
        stmt = select(TradingAccount.pending_earnings_usdt).where(TradingAccount.id == self._account_id)
        result = await self._session.execute(stmt)
        val = result.scalar_one_or_none()
        return float(val) if val is not None else 0.0

    async def add_pending_earnings(self, delta: float) -> None:
        """원자적 증감 - 동시성 안전 (trading loop + approve 경합 방지)"""
        stmt = (
            update(TradingAccount)
            .where(TradingAccount.id == self._account_id)
            .values(pending_earnings_usdt=TradingAccount.pending_earnings_usdt + delta)
        )
        await self._session.execute(stmt)

    async def reset_pending_earnings(self) -> None:
        """적립금 리셋 (approve 결정 후)"""
        stmt = update(TradingAccount).where(TradingAccount.id == self._account_id).values(pending_earnings_usdt=0)
        await self._session.execute(stmt)

    async def approve_earnings_to_reserve(self, pct: float, current_price: float) -> dict:
        """
        적립금의 pct%를 reserve에 추가.
        SELECT FOR UPDATE로 approve 중 다른 트랜잭션의 pending_earnings 수정 방지.
        결정 후 pending_earnings는 무조건 0으로 리셋.
        """
        stmt = (
            select(TradingAccount.pending_earnings_usdt).where(TradingAccount.id == self._account_id).with_for_update()
        )
        result = await self._session.execute(stmt)
        total_earnings = float(result.scalar_one())

        if total_earnings <= 0:
            raise ValueError("적립금이 없습니다.")

        to_reserve_usdt = total_earnings * (pct / 100.0)
        to_liquid_usdt = total_earnings - to_reserve_usdt
        to_reserve_btc = to_reserve_usdt / current_price if current_price > 0 else 0.0

        if to_reserve_usdt > 0:
            await self.add_reserve_qty(to_reserve_btc)
            await self.add_reserve_cost_usdt(to_reserve_usdt)

        await self.reset_pending_earnings()

        return {
            "total_earnings": total_earnings,
            "to_reserve_usdt": to_reserve_usdt,
            "to_reserve_btc": to_reserve_btc,
            "to_liquid_usdt": to_liquid_usdt,
            "reserve_pct": pct,
        }
