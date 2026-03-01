"""Unit tests for backtest in-memory stores.

All async methods tested via asyncio.run() wrapper (no pytest-asyncio dependency).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from backtest.mem_stores import (
    InMemoryAccountStateManager,
    InMemoryLotRepository,
    InMemoryOrderRepository,
    InMemoryStateStore,
    _NoOpSession,
)
import app.strategies.state_store as _ss_mod


def run(coro):
    """Helper to run async coroutines in sync test functions."""
    return asyncio.run(coro)


# -----------------------------------------------------------------------
# InMemoryStateStore
# -----------------------------------------------------------------------


class TestInMemoryStateStore:
    def test_set_get_returns_string(self):
        """set(key, 42.5) → get(key) returns '42.5' (str, not float)."""
        backing = {}
        store = InMemoryStateStore(uuid4(), "test", backing)

        async def go():
            await store.set("price", 42.5)
            val = await store.get("price")
            assert val == "42.5"
            assert isinstance(val, str)

        run(go())

    def test_get_float_parses_string(self):
        """set(key, '3.14') → get_float(key) returns 3.14."""
        backing = {}
        store = InMemoryStateStore(uuid4(), "test", backing)

        async def go():
            await store.set("pi", "3.14")
            val = await store.get_float("pi")
            assert abs(val - 3.14) < 1e-9

        run(go())

    def test_get_int_parses_string(self):
        """set(key, '7') → get_int(key) returns 7."""
        backing = {}
        store = InMemoryStateStore(uuid4(), "test", backing)

        async def go():
            await store.set("round", "7")
            val = await store.get_int("round")
            assert val == 7

        run(go())

    def test_clear_keys_sets_empty_string(self):
        """clear_keys sets empty string, not deletes — matches DB behavior."""
        backing = {}
        store = InMemoryStateStore(uuid4(), "test", backing)

        async def go():
            await store.set("pending_order_id", "12345")
            await store.clear_keys("pending_order_id")
            val = await store.get("pending_order_id")
            assert val == ""
            # get_float on empty string returns default
            fval = await store.get_float("pending_order_id", 0.0)
            assert fval == 0.0

        run(go())

    def test_cross_scope_isolation(self):
        """Different scopes can't read each other's keys."""
        backing = {}
        aid = uuid4()
        store_a = InMemoryStateStore(aid, "combo_a", backing)
        store_b = InMemoryStateStore(aid, "combo_b", backing)

        async def go():
            await store_a.set("base_price", "100.0")
            val = await store_b.get("base_price")
            assert val is None

        run(go())

    def test_shared_backing_cross_combo(self):
        """Two stores with different scopes but same backing can co-exist."""
        backing = {}
        aid = uuid4()
        store_a = InMemoryStateStore(aid, "combo_a", backing)
        store_b = InMemoryStateStore(aid, "combo_b", backing)

        async def go():
            await store_a.set("base_price", "100.0")
            await store_b.set("base_price", "200.0")
            assert await store_a.get_float("base_price") == 100.0
            assert await store_b.get_float("base_price") == 200.0

        run(go())

    def test_session_add_noop(self):
        """state._session.add() collects objects without error."""
        backing = {}
        store = InMemoryStateStore(uuid4(), "test", backing)
        assert isinstance(store._session, _NoOpSession)
        store._session.add({"fake": "object"})
        assert len(store._session._added) == 1

    def test_get_all(self):
        """get_all returns all keys in scope without prefix."""
        backing = {}
        store = InMemoryStateStore(uuid4(), "test", backing)

        async def go():
            await store.set("a", "1")
            await store.set("b", "2")
            result = await store.get_all()
            assert result == {"a": "1", "b": "2"}

        run(go())

    def test_delete(self):
        """delete removes key entirely (get returns default)."""
        backing = {}
        store = InMemoryStateStore(uuid4(), "test", backing)

        async def go():
            await store.set("x", "10")
            await store.delete("x")
            assert await store.get("x") is None

        run(go())


# -----------------------------------------------------------------------
# InMemoryLotRepository
# -----------------------------------------------------------------------


class TestInMemoryLotRepository:
    def test_insert_lot_auto_increment(self):
        """lot_id auto-increments starting from 1."""
        repo = InMemoryLotRepository()
        aid = uuid4()
        cid = uuid4()

        async def go():
            lot1 = await repo.insert_lot(
                account_id=aid, symbol="ETHUSDT", strategy_name="lot_stacking",
                buy_order_id=100, buy_price=2000.0, buy_qty=0.01,
                buy_time_ms=1000, combo_id=cid,
            )
            lot2 = await repo.insert_lot(
                account_id=aid, symbol="ETHUSDT", strategy_name="lot_stacking",
                buy_order_id=101, buy_price=1990.0, buy_qty=0.01,
                buy_time_ms=2000, combo_id=cid,
            )
            assert lot1.lot_id == 1
            assert lot2.lot_id == 2

        run(go())

    def test_get_open_lots_by_combo_sorted(self):
        """Returns only OPEN lots for given combo, sorted by buy_time_ms."""
        repo = InMemoryLotRepository()
        aid = uuid4()
        cid1 = uuid4()
        cid2 = uuid4()

        async def go():
            # Insert lots for two combos, out of order
            await repo.insert_lot(
                account_id=aid, symbol="ETHUSDT", strategy_name="lot_stacking",
                buy_order_id=1, buy_price=2000.0, buy_qty=0.01,
                buy_time_ms=3000, combo_id=cid1,
            )
            await repo.insert_lot(
                account_id=aid, symbol="ETHUSDT", strategy_name="lot_stacking",
                buy_order_id=2, buy_price=1990.0, buy_qty=0.01,
                buy_time_ms=1000, combo_id=cid1,
            )
            await repo.insert_lot(
                account_id=aid, symbol="ETHUSDT", strategy_name="lot_stacking",
                buy_order_id=3, buy_price=2010.0, buy_qty=0.01,
                buy_time_ms=2000, combo_id=cid2,
            )

            lots = await repo.get_open_lots_by_combo(aid, "ETHUSDT", cid1)
            assert len(lots) == 2
            assert lots[0].buy_time_ms == 1000  # sorted ascending
            assert lots[1].buy_time_ms == 3000

        run(go())

    def test_close_lot_excludes_from_open(self):
        """Closed lots are excluded from get_open_lots_by_combo."""
        repo = InMemoryLotRepository()
        aid = uuid4()
        cid = uuid4()

        async def go():
            lot = await repo.insert_lot(
                account_id=aid, symbol="ETHUSDT", strategy_name="lot_stacking",
                buy_order_id=1, buy_price=2000.0, buy_qty=0.01,
                buy_time_ms=1000, combo_id=cid,
            )
            await repo.close_lot(
                account_id=aid, lot_id=lot.lot_id,
                sell_price=2066.0, sell_time_ms=5000,
                fee_usdt=0.1, net_profit_usdt=0.56,
            )
            open_lots = await repo.get_open_lots_by_combo(aid, "ETHUSDT", cid)
            assert len(open_lots) == 0
            # Verify closed lot attributes
            closed = repo._lots[(lot.lot_id, aid)]
            assert closed.status == "CLOSED"
            assert closed.sell_price == 2066.0
            assert closed.sell_time is not None

        run(go())

    def test_set_clear_sell_order(self):
        """set_sell_order and clear_sell_order update lot correctly."""
        repo = InMemoryLotRepository()
        aid = uuid4()
        cid = uuid4()

        async def go():
            lot = await repo.insert_lot(
                account_id=aid, symbol="ETHUSDT", strategy_name="lot_stacking",
                buy_order_id=1, buy_price=2000.0, buy_qty=0.01,
                buy_time_ms=1000, combo_id=cid,
            )
            await repo.set_sell_order(
                account_id=aid, lot_id=lot.lot_id,
                sell_order_id=999, sell_order_time_ms=2000,
            )
            assert lot.sell_order_id == 999
            assert lot.sell_order_time_ms == 2000

            await repo.clear_sell_order(account_id=aid, lot_id=lot.lot_id)
            assert lot.sell_order_id is None
            assert lot.sell_order_time_ms is None

        run(go())


# -----------------------------------------------------------------------
# InMemoryOrderRepository
# -----------------------------------------------------------------------


class TestInMemoryOrderRepository:
    def test_upsert_and_get(self):
        """upsert_order then get_order returns MemOrder with correct attributes."""
        repo = InMemoryOrderRepository()
        aid = uuid4()

        async def go():
            await repo.upsert_order(aid, {
                "orderId": 12345,
                "symbol": "ETHUSDT",
                "side": "BUY",
                "type": "LIMIT",
                "status": "FILLED",
                "price": "2000.50",
                "origQty": "0.01",
                "executedQty": "0.01",
                "cummulativeQuoteQty": "20.005",
                "clientOrderId": "bt_test_1",
                "updateTime": 1700000000000,
            })
            order = await repo.get_order(aid, 12345)
            assert order is not None
            assert order.symbol == "ETHUSDT"
            assert order.side == "BUY"
            assert order.status == "FILLED"
            assert order.price == 2000.50
            assert order.executed_qty == 0.01

        run(go())

    def test_upsert_overwrites(self):
        """Same order_id upsert overwrites previous data."""
        repo = InMemoryOrderRepository()
        aid = uuid4()

        async def go():
            base = {
                "orderId": 100,
                "symbol": "ETHUSDT",
                "side": "BUY",
                "type": "LIMIT",
                "status": "NEW",
                "price": "2000",
                "origQty": "0.01",
                "executedQty": "0",
                "cummulativeQuoteQty": "0",
            }
            await repo.upsert_order(aid, base)
            order = await repo.get_order(aid, 100)
            assert order.status == "NEW"

            await repo.upsert_order(aid, {**base, "status": "FILLED", "executedQty": "0.01"})
            order = await repo.get_order(aid, 100)
            assert order.status == "FILLED"
            assert order.executed_qty == 0.01

        run(go())


# -----------------------------------------------------------------------
# InMemoryAccountStateManager
# -----------------------------------------------------------------------


class TestInMemoryAccountStateManager:
    def test_reserve_via_shared_store(self):
        """Reserve operations use shared scope and accumulate correctly."""
        backing = {}
        aid = uuid4()
        asm = InMemoryAccountStateManager(aid, backing)

        async def go():
            assert await asm.get_reserve_qty() == 0.0
            await asm.set_reserve_qty(1.5)
            assert await asm.get_reserve_qty() == 1.5
            result = await asm.add_reserve_qty(0.5)
            assert result == 2.0
            assert await asm.get_reserve_qty() == 2.0

            # Cost
            await asm.set_reserve_cost_usdt(3000.0)
            result = await asm.add_reserve_cost_usdt(500.0)
            assert result == 3500.0

        run(go())

    def test_pending_earnings_accumulate(self):
        """Pending earnings accumulate and reset correctly."""
        backing = {}
        aid = uuid4()
        asm = InMemoryAccountStateManager(aid, backing)

        async def go():
            assert await asm.get_pending_earnings() == 0.0
            await asm.add_pending_earnings(10.5)
            await asm.add_pending_earnings(5.25)
            assert await asm.get_pending_earnings() == 15.75
            await asm.reset_pending_earnings()
            assert await asm.get_pending_earnings() == 0.0

        run(go())

    def test_reserve_shared_with_state_store(self):
        """AccountStateManager's reserve is visible via InMemoryStateStore with shared scope."""
        backing = {}
        aid = uuid4()
        asm = InMemoryAccountStateManager(aid, backing)
        shared_store = InMemoryStateStore(aid, "shared", backing)

        async def go():
            await asm.set_reserve_qty(2.0)
            # Read through a separate StateStore instance with same backing
            val = await shared_store.get_float("reserve_qty")
            assert val == 2.0

        run(go())


# -----------------------------------------------------------------------
# Cross-combo integration (module-level patch simulation)
# -----------------------------------------------------------------------


class TestCrossComboIntegration:
    def test_module_patch_enables_cross_combo_read(self):
        """Simulate trend.py:252 cross-combo state read via module-level patch."""
        backing = {}
        aid = uuid4()
        lot_combo_id = uuid4()
        trend_combo_id = uuid4()

        # lot_stacking writes base_price to its combo scope
        lot_state = InMemoryStateStore(aid, str(lot_combo_id), backing)

        # Patch module-level StrategyStateStore like isolated_runner does
        original_ss = _ss_mod.StrategyStateStore
        _ss_mod.StrategyStateStore = (
            lambda a, scope, session: InMemoryStateStore(a, scope, backing)
        )

        try:
            async def go():
                await lot_state.set("base_price", 2500.0)

                # Simulate trend.py:246-252 — lazy import + cross-combo read
                from app.strategies.state_store import StrategyStateStore as SSStore
                ref_state = SSStore(aid, str(lot_combo_id), lot_state._session)
                base_price = await ref_state.get_float("base_price", 0.0)
                assert base_price == 2500.0

                # Trend's own scope is isolated
                trend_state = InMemoryStateStore(aid, str(trend_combo_id), backing)
                assert await trend_state.get_float("base_price", 0.0) == 0.0

            run(go())
        finally:
            _ss_mod.StrategyStateStore = original_ss

    def test_noop_session_add_compatible(self):
        """state._session.add(CoreBtcHistory) pattern works without error."""
        backing = {}
        state = InMemoryStateStore(uuid4(), "test", backing)

        # Simulate lot_stacking.py:252-259
        class FakeCoreBtcHistory:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        history = FakeCoreBtcHistory(
            account_id=uuid4(), symbol="ETHUSDT",
            btc_qty=0.01, cost_usdt=20.0, source="INIT",
        )
        state._session.add(history)
        assert len(state._session._added) == 1
        assert state._session._added[0].btc_qty == 0.01
