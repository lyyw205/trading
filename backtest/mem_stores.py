"""In-memory implementations of repository and state interfaces for backtesting.

Replaces DB-backed LotRepository, OrderRepository, StrategyStateStore,
and AccountStateManager with pure-dict implementations. All methods maintain
identical async signatures so strategy code requires zero modifications.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID


# ---------------------------------------------------------------------------
# _NoOpSession — state._session.add() 호환
# ---------------------------------------------------------------------------

class _NoOpSession:
    """state._session.add(obj) 호환용 no-op sink.

    lot_stacking.py:259 에서 CoreBtcHistory를 session.add() 하는 패턴 대응.
    감사 로그 전용이므로 수집만 하고 DB 저장하지 않는다.
    """

    def __init__(self) -> None:
        self._added: list = []

    def add(self, obj: object) -> None:
        self._added.append(obj)


# ---------------------------------------------------------------------------
# InMemoryStateStore — StrategyStateStore 대체
# ---------------------------------------------------------------------------

class InMemoryStateStore:
    """StrategyStateStore와 동일 인터페이스, dict 기반.

    모든 combo가 공유하는 단일 backing dict를 사용하여
    cross-combo state 읽기 (trend.py:252)를 지원한다.
    scope별 격리는 _prefix 기반.
    """

    def __init__(
        self,
        account_id: UUID,
        scope: str,
        backing: dict[str, str],
    ) -> None:
        self.account_id = account_id
        self.scope = scope
        self._backing = backing
        self._prefix = f"{account_id}:{scope}:"
        self._session = _NoOpSession()

    async def get(self, key: str, default: str | None = None) -> str | None:
        return self._backing.get(self._prefix + key, default)

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

    async def set(self, key: str, value: object) -> None:
        # CRITICAL: DB 구현과 동일하게 str(value)로 변환 저장
        self._backing[self._prefix + key] = str(value)

    async def delete(self, key: str) -> None:
        self._backing.pop(self._prefix + key, None)

    async def clear_keys(self, *keys: str) -> None:
        # DB 구현과 동일: 빈 문자열로 설정 (삭제가 아님)
        for key in keys:
            await self.set(key, "")

    async def get_all(self) -> dict[str, str]:
        prefix = self._prefix
        plen = len(prefix)
        return {
            k[plen:]: v
            for k, v in self._backing.items()
            if k.startswith(prefix)
        }


# ---------------------------------------------------------------------------
# MemLot — Lot 모델 대체
# ---------------------------------------------------------------------------

@dataclass
class MemLot:
    """Lot SQLAlchemy 모델과 동일 속성. float 타입으로 산술 호환."""

    lot_id: int
    account_id: UUID
    symbol: str
    strategy_name: str
    buy_order_id: int | None
    buy_price: float
    buy_qty: float
    buy_time: datetime
    buy_time_ms: int | None
    status: str  # "OPEN" | "CLOSED"
    combo_id: UUID | None
    sell_order_id: int | None = None
    sell_order_time_ms: int | None = None
    sell_price: float | None = None
    sell_time: datetime | None = None
    sell_time_ms: int | None = None
    fee_usdt: float | None = None
    net_profit_usdt: float | None = None
    metadata_: dict | None = None


# ---------------------------------------------------------------------------
# InMemoryLotRepository — LotRepository 대체
# ---------------------------------------------------------------------------

class InMemoryLotRepository:
    def __init__(self) -> None:
        self._lots: dict[tuple[int, UUID], MemLot] = {}
        self._next_lot_id: int = 1

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
    ) -> MemLot:
        lot_id = self._next_lot_id
        self._next_lot_id += 1
        lot = MemLot(
            lot_id=lot_id,
            account_id=account_id,
            symbol=symbol,
            strategy_name=strategy_name,
            buy_order_id=buy_order_id,
            buy_price=float(buy_price),
            buy_qty=float(buy_qty),
            buy_time=datetime.now(timezone.utc),
            buy_time_ms=buy_time_ms,
            status="OPEN",
            combo_id=combo_id,
        )
        self._lots[(lot_id, account_id)] = lot
        return lot

    async def get_open_lots_by_combo(
        self,
        account_id: UUID,
        symbol: str,
        combo_id: UUID,
    ) -> list[MemLot]:
        return sorted(
            [
                lot for lot in self._lots.values()
                if lot.account_id == account_id
                and lot.symbol == symbol
                and lot.combo_id == combo_id
                and lot.status == "OPEN"
            ],
            key=lambda lot: lot.buy_time_ms or 0,
        )

    async def get_open_lots(
        self,
        account_id: UUID,
        symbol: str,
        strategy_name: str = "lot_stacking",
    ) -> list[MemLot]:
        return sorted(
            [
                lot for lot in self._lots.values()
                if lot.account_id == account_id
                and lot.symbol == symbol
                and lot.strategy_name == strategy_name
                and lot.status == "OPEN"
            ],
            key=lambda lot: lot.lot_id,
        )

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
        key = (lot_id, account_id)
        lot = self._lots[key]
        lot.status = "CLOSED"
        lot.sell_price = float(sell_price)
        lot.sell_time = datetime.now(timezone.utc)
        lot.sell_time_ms = sell_time_ms
        lot.fee_usdt = float(fee_usdt)
        lot.net_profit_usdt = float(net_profit_usdt)
        lot.sell_order_id = sell_order_id

    async def set_sell_order(
        self,
        *,
        account_id: UUID,
        lot_id: int,
        sell_order_id: int,
        sell_order_time_ms: int,
    ) -> None:
        key = (lot_id, account_id)
        lot = self._lots[key]
        lot.sell_order_id = sell_order_id
        lot.sell_order_time_ms = sell_order_time_ms

    async def clear_sell_order(
        self,
        *,
        account_id: UUID,
        lot_id: int,
    ) -> None:
        key = (lot_id, account_id)
        lot = self._lots[key]
        lot.sell_order_id = None
        lot.sell_order_time_ms = None


# ---------------------------------------------------------------------------
# MemOrder — Order 모델 대체
# ---------------------------------------------------------------------------

@dataclass
class MemOrder:
    """Order SQLAlchemy 모델과 동일 속성."""

    order_id: int
    account_id: UUID
    symbol: str
    side: str | None = None
    type: str | None = None
    status: str | None = None
    price: float | None = None
    orig_qty: float | None = None
    executed_qty: float | None = None
    cum_quote_qty: float | None = None
    client_order_id: str | None = None
    update_time_ms: int | None = None
    raw_json: dict | None = None


# ---------------------------------------------------------------------------
# InMemoryOrderRepository — OrderRepository 대체
# ---------------------------------------------------------------------------

class InMemoryOrderRepository:
    def __init__(self) -> None:
        self._orders: dict[tuple[int, UUID], MemOrder] = {}

    async def upsert_order(self, account_id: UUID, order_data: dict) -> None:
        order_id = int(order_data["orderId"])
        self._orders[(order_id, account_id)] = MemOrder(
            order_id=order_id,
            account_id=account_id,
            symbol=order_data["symbol"],
            side=order_data.get("side"),
            type=order_data.get("type"),
            status=order_data.get("status"),
            price=(
                float(order_data["price"])
                if order_data.get("price") is not None
                else None
            ),
            orig_qty=(
                float(order_data["origQty"])
                if order_data.get("origQty") is not None
                else None
            ),
            executed_qty=(
                float(order_data["executedQty"])
                if order_data.get("executedQty") is not None
                else None
            ),
            cum_quote_qty=(
                float(order_data["cummulativeQuoteQty"])
                if order_data.get("cummulativeQuoteQty") is not None
                else None
            ),
            client_order_id=order_data.get("clientOrderId"),
            update_time_ms=(
                int(order_data["updateTime"])
                if order_data.get("updateTime") is not None
                else None
            ),
            raw_json=order_data,
        )

    async def get_order(
        self, account_id: UUID, order_id: int
    ) -> MemOrder | None:
        return self._orders.get((order_id, account_id))


# ---------------------------------------------------------------------------
# InMemoryAccountStateManager — AccountStateManager 대체
# ---------------------------------------------------------------------------

class InMemoryAccountStateManager:
    """AccountStateManager와 동일 인터페이스.

    reserve 계열: InMemoryStateStore(scope="shared") 사용 → combo 간 공유.
    pending_earnings: 단순 float 변수 (단일 스레드 backtest이므로 atomic 불필요).
    """

    def __init__(self, account_id: UUID, backing: dict[str, str]) -> None:
        self._account_id = account_id
        self._store = InMemoryStateStore(account_id, "shared", backing)
        self._pending_earnings: float = 0.0

    # ---- reserve (AccountStateManager와 동일 로직) ----

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

    # ---- pending_earnings (DB UPDATE 대신 float 변수) ----

    async def get_pending_earnings(self) -> float:
        return self._pending_earnings

    async def add_pending_earnings(self, delta: float) -> None:
        self._pending_earnings += delta

    async def reset_pending_earnings(self) -> None:
        self._pending_earnings = 0.0

    async def approve_earnings_to_reserve(
        self, pct: float, current_price: float
    ) -> dict:
        total = self._pending_earnings
        if total <= 0:
            raise ValueError("적립금이 없습니다.")
        to_reserve_usdt = total * (pct / 100.0)
        to_liquid_usdt = total - to_reserve_usdt
        to_reserve_btc = (
            to_reserve_usdt / current_price if current_price > 0 else 0.0
        )
        if to_reserve_usdt > 0:
            await self.add_reserve_qty(to_reserve_btc)
            await self.add_reserve_cost_usdt(to_reserve_usdt)
        await self.reset_pending_earnings()
        return {
            "total_earnings": total,
            "to_reserve_usdt": to_reserve_usdt,
            "to_reserve_btc": to_reserve_btc,
            "to_liquid_usdt": to_liquid_usdt,
            "reserve_pct": pct,
        }
