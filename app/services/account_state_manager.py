from __future__ import annotations
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from app.strategies.state_store import StrategyStateStore


class AccountStateManager:
    """
    계정 레벨 공유 상태 관리.
    reserve pool(reserve_qty, reserve_cost_usdt)은 LOT/TREND 양쪽에서 접근.
    이 매니저를 통해 원자적으로 읽기/쓰기하여 경합 방지.
    내부적으로 StrategyStateStore(scope='shared')를 사용.
    """

    def __init__(self, account_id: UUID, session: AsyncSession):
        self._store = StrategyStateStore(account_id, scope="shared", session=session)

    async def get_reserve_qty(self) -> float:
        return await self._store.get_float("reserve_qty", 0.0)

    async def set_reserve_qty(self, qty: float) -> None:
        await self._store.set("reserve_qty", float(qty))

    async def add_reserve_qty(self, delta: float) -> float:
        current = await self.get_reserve_qty()
        new_val = current + delta
        await self.set_reserve_qty(new_val)
        return new_val

    async def get_reserve_cost_usdt(self) -> float:
        return await self._store.get_float("reserve_cost_usdt", 0.0)

    async def set_reserve_cost_usdt(self, cost: float) -> None:
        await self._store.set("reserve_cost_usdt", float(cost))

    async def add_reserve_cost_usdt(self, delta: float) -> float:
        current = await self.get_reserve_cost_usdt()
        new_val = current + delta
        await self.set_reserve_cost_usdt(new_val)
        return new_val
