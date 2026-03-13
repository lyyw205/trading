"""Tests for dust lot duplicate prevention guard.

Task 1: InMemoryLotRepository.insert_lot duplicate guard
Task 2: lot_stacking _handle_filled_buy duplicate call scenario
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.backtest.mem_stores import InMemoryLotRepository

# ---------------------------------------------------------------------------
# Task 1: InMemoryLotRepository duplicate guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInMemoryDuplicateGuard:
    @pytest.mark.asyncio
    async def test_duplicate_buy_order_id_returns_existing(self):
        """Same buy_order_id + account_id → only 1 lot created, second call returns first."""
        repo = InMemoryLotRepository()
        account_id = uuid4()

        lot1 = await repo.insert_lot(
            account_id=account_id,
            symbol="ETHUSDT",
            strategy_name="lot_stacking",
            buy_order_id=12345,
            buy_price=2000.0,
            buy_qty=0.01,
            buy_time_ms=1000,
        )
        lot2 = await repo.insert_lot(
            account_id=account_id,
            symbol="ETHUSDT",
            strategy_name="lot_stacking",
            buy_order_id=12345,
            buy_price=2000.0,
            buy_qty=0.005,
            buy_time_ms=1001,
        )

        assert lot1.lot_id == lot2.lot_id
        assert lot1 is lot2
        # Only 1 lot exists
        all_open = await repo.get_open_lots(account_id, "ETHUSDT")
        assert len(all_open) == 1

    @pytest.mark.asyncio
    async def test_none_buy_order_id_allows_duplicates(self):
        """buy_order_id=None → guard not applied, multiple lots created."""
        repo = InMemoryLotRepository()
        account_id = uuid4()

        lot1 = await repo.insert_lot(
            account_id=account_id,
            symbol="ETHUSDT",
            strategy_name="lot_stacking",
            buy_order_id=None,
            buy_price=2000.0,
            buy_qty=0.01,
            buy_time_ms=1000,
        )
        lot2 = await repo.insert_lot(
            account_id=account_id,
            symbol="ETHUSDT",
            strategy_name="lot_stacking",
            buy_order_id=None,
            buy_price=2000.0,
            buy_qty=0.01,
            buy_time_ms=1001,
        )

        assert lot1.lot_id != lot2.lot_id
        all_open = await repo.get_open_lots(account_id, "ETHUSDT")
        assert len(all_open) == 2

    @pytest.mark.asyncio
    async def test_different_account_same_buy_order_id(self):
        """Different account_id with same buy_order_id → separate lots."""
        repo = InMemoryLotRepository()
        account_a = uuid4()
        account_b = uuid4()

        lot1 = await repo.insert_lot(
            account_id=account_a,
            symbol="ETHUSDT",
            strategy_name="lot_stacking",
            buy_order_id=12345,
            buy_price=2000.0,
            buy_qty=0.01,
            buy_time_ms=1000,
        )
        lot2 = await repo.insert_lot(
            account_id=account_b,
            symbol="ETHUSDT",
            strategy_name="lot_stacking",
            buy_order_id=12345,
            buy_price=2000.0,
            buy_qty=0.01,
            buy_time_ms=1000,
        )

        assert lot1.lot_id != lot2.lot_id

    @pytest.mark.asyncio
    async def test_closed_lot_allows_new_with_same_buy_order_id(self):
        """After closing a lot, same buy_order_id can create a new OPEN lot."""
        repo = InMemoryLotRepository()
        account_id = uuid4()

        lot1 = await repo.insert_lot(
            account_id=account_id,
            symbol="ETHUSDT",
            strategy_name="lot_stacking",
            buy_order_id=12345,
            buy_price=2000.0,
            buy_qty=0.01,
            buy_time_ms=1000,
        )

        # Close the lot
        await repo.close_lot(
            account_id=account_id,
            lot_id=lot1.lot_id,
            sell_price=2100.0,
            sell_time_ms=2000,
            fee_usdt=0.01,
            net_profit_usdt=1.0,
        )

        # New lot with same buy_order_id should succeed
        lot2 = await repo.insert_lot(
            account_id=account_id,
            symbol="ETHUSDT",
            strategy_name="lot_stacking",
            buy_order_id=12345,
            buy_price=2050.0,
            buy_qty=0.01,
            buy_time_ms=3000,
        )

        assert lot1.lot_id != lot2.lot_id
        assert lot2.status == "OPEN"


# ---------------------------------------------------------------------------
# Task 2: lot_stacking _handle_filled_buy duplicate scenario
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleFilledBuyDuplicate:
    @pytest.mark.asyncio
    async def test_duplicate_handle_filled_buy_creates_single_lot(self):
        """Calling _handle_filled_buy twice with same order → only 1 lot."""
        from unittest.mock import AsyncMock, MagicMock

        from app.strategies.buys.lot_stacking import LotStackingBuy

        strategy = LotStackingBuy()

        account_id = uuid4()
        combo_id = uuid4()
        ctx = MagicMock()
        ctx.account_id = account_id
        ctx.symbol = "ETHUSDT"
        ctx.base_asset = "ETH"
        ctx.current_price = 2000.0
        ctx.params = {"sizing_mode": "fixed"}

        state = AsyncMock()
        state.set = AsyncMock()
        state.get_int = AsyncMock(return_value=1)
        state.session = MagicMock()
        state.session.add = MagicMock()
        state.clear_keys = AsyncMock()

        lot_repo = InMemoryLotRepository()
        repos = MagicMock()
        repos.lot = lot_repo
        repos.order = AsyncMock()
        repos.order.upsert_order = AsyncMock()

        account_state = AsyncMock()

        order_data = {
            "orderId": 99999,
            "status": "FILLED",
            "executedQty": "0.01",
            "cummulativeQuoteQty": "20.0",
            "updateTime": 1700000000000,
            "fills": [],
        }

        # Call twice with same order
        await strategy._handle_filled_buy(ctx, state, order_data, account_state, repos, combo_id, kind="LOT")
        await strategy._handle_filled_buy(ctx, state, order_data, account_state, repos, combo_id, kind="LOT")

        all_lots = await lot_repo.get_open_lots(account_id, "ETHUSDT")
        assert len(all_lots) == 1
        assert all_lots[0].buy_order_id == 99999
