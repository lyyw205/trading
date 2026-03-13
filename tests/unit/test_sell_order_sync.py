"""
Tests for sell order sync fixes:
- Task 1: Atomic flush after sell order DB writes, no cancel on failure
- Task 2: Retry limiting with cooldown and reset
- Task 3: Orphaned order recovery via _reconcile_orphan_sells
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.strategies.sells.fixed_tp import FixedTpSell

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(account_id=None, symbol="ETHUSDT", prefix="CMT_test_"):
    """Build a minimal StrategyContext-like object."""
    ctx = MagicMock()
    ctx.account_id = account_id or uuid4()
    ctx.symbol = symbol
    ctx.base_asset = "ETH"
    ctx.quote_asset = "USDT"
    ctx.current_price = 2000.0
    ctx.params = {"tp_pct": 0.033}
    ctx.client_order_prefix = prefix
    return ctx


def _make_lot(lot_id=1, buy_price=2000.0, buy_qty=0.01, sell_order_id=None):
    lot = MagicMock()
    lot.lot_id = lot_id
    lot.buy_price = buy_price
    lot.buy_qty = buy_qty
    lot.sell_order_id = sell_order_id
    return lot


def _make_repos():
    repos = MagicMock()
    repos.order = AsyncMock()
    repos.order.upsert_order = AsyncMock()
    repos.lot = AsyncMock()
    repos.lot.set_sell_order = AsyncMock()
    repos.lot.flush = AsyncMock()
    return repos


def _make_state():
    """StrategyStateStore mock with in-memory dict storage."""
    store = {}
    state = AsyncMock()

    async def _get_int(key, default=0):
        val = store.get(key)
        return int(float(val)) if val is not None else default

    async def _get_float(key, default=0.0):
        val = store.get(key)
        return float(val) if val is not None else default

    async def _set(key, value):
        store[key] = str(value)

    async def _set_many(items):
        for k, v in items.items():
            store[k] = str(v)

    async def _clear_keys(*keys):
        for k in keys:
            store.pop(k, None)

    state.get_int = AsyncMock(side_effect=_get_int)
    state.get_float = AsyncMock(side_effect=_get_float)
    state.set = AsyncMock(side_effect=_set)
    state.set_many = AsyncMock(side_effect=_set_many)
    state.clear_keys = AsyncMock(side_effect=_clear_keys)
    state._store = store  # expose for assertions
    return state


def _make_exchange(sell_resp=None):
    exchange = AsyncMock()
    if sell_resp is None:
        sell_resp = {
            "orderId": 12345,
            "status": "NEW",
            "transactTime": 1700000000000,
        }
    exchange.place_limit_sell = AsyncMock(return_value=sell_resp)
    exchange.cancel_order = AsyncMock()
    return exchange


def _make_sell_strategy(sim_time=1000.0):
    """Create a FixedTpSell with _sim_time set so cooldown is skipped."""
    strategy = FixedTpSell()
    strategy._sim_time = sim_time
    strategy._last_order_ts = 0.0
    return strategy


# ===========================================================================
# Task 1: Atomic flush, no cancel on failure
# ===========================================================================


@pytest.mark.unit
class TestAtomicFlush:
    @pytest.mark.asyncio
    async def test_flush_called_after_set_sell_order(self):
        """flush() is called after upsert_order + set_sell_order."""
        strategy = _make_sell_strategy()
        ctx = _make_ctx()
        state = _make_state()
        repos = _make_repos()
        exchange = _make_exchange()
        lot = _make_lot()

        await strategy._place_new_sell_order(
            ctx,
            state,
            exchange,
            repos,
            lot,
            2066.0,
            0.01,
        )

        repos.order.upsert_order.assert_awaited_once()
        repos.lot.set_sell_order.assert_awaited_once()
        repos.lot.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_flush_failure_does_not_cancel_binance_order(self):
        """On flush failure, Binance order is NOT cancelled."""
        strategy = _make_sell_strategy()
        ctx = _make_ctx()
        state = _make_state()
        repos = _make_repos()
        repos.lot.flush = AsyncMock(side_effect=Exception("DB connection lost"))
        exchange = _make_exchange()
        lot = _make_lot()

        # Should not raise
        await strategy._place_new_sell_order(
            ctx,
            state,
            exchange,
            repos,
            lot,
            2066.0,
            0.01,
        )

        # Exchange cancel must NOT be called
        exchange.cancel_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flush_failure_does_not_propagate(self):
        """Flush failure does not propagate — cycle continues."""
        strategy = _make_sell_strategy()
        ctx = _make_ctx()
        state = _make_state()
        repos = _make_repos()
        repos.lot.flush = AsyncMock(side_effect=Exception("DB error"))
        exchange = _make_exchange()
        lot = _make_lot()

        # No exception should escape
        try:
            await strategy._place_new_sell_order(
                ctx,
                state,
                exchange,
                repos,
                lot,
                2066.0,
                0.01,
            )
        except Exception:
            pytest.fail("flush failure should not propagate")


# ===========================================================================
# Task 2: Retry limiting with cooldown reset
# ===========================================================================


@pytest.mark.unit
class TestRetryLimiting:
    @pytest.mark.asyncio
    async def test_retry_counter_increments_on_failure(self):
        """Each sell placement failure increments the retry counter."""
        strategy = _make_sell_strategy()
        ctx = _make_ctx()
        state = _make_state()
        repos = _make_repos()
        exchange = _make_exchange()
        exchange.place_limit_sell = AsyncMock(side_effect=Exception("insufficient balance"))
        lot = _make_lot(lot_id=42)

        for _i in range(3):
            await strategy._place_new_sell_order(
                ctx,
                state,
                exchange,
                repos,
                lot,
                2066.0,
                0.01,
            )

        assert state._store.get("sell_retry_count:42") == "3"
        assert "sell_retry_after:42" in state._store

    @pytest.mark.asyncio
    async def test_placement_skipped_after_max_retries(self):
        """After max retries with active cooldown, placement is skipped."""
        strategy = _make_sell_strategy(sim_time=1000.0)
        ctx = _make_ctx()
        state = _make_state()
        # Pre-set retry state: 3 failures, cooldown until far future
        state._store["sell_retry_count:42"] = "3"
        state._store["sell_retry_after:42"] = str(9999999999.0)
        repos = _make_repos()
        exchange = _make_exchange()
        lot = _make_lot(lot_id=42)

        await strategy._place_new_sell_order(
            ctx,
            state,
            exchange,
            repos,
            lot,
            2066.0,
            0.01,
        )

        # Exchange should NOT be called
        exchange.place_limit_sell.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cooldown_expired_resets_counter(self):
        """After cooldown expires, counter resets and placement is retried."""
        strategy = _make_sell_strategy(sim_time=1000.0)
        ctx = _make_ctx()
        state = _make_state()
        # Pre-set retry state: 3 failures, cooldown already expired
        state._store["sell_retry_count:42"] = "3"
        state._store["sell_retry_after:42"] = str(500.0)  # in the past
        repos = _make_repos()
        exchange = _make_exchange()
        lot = _make_lot(lot_id=42)

        await strategy._place_new_sell_order(
            ctx,
            state,
            exchange,
            repos,
            lot,
            2066.0,
            0.01,
        )

        # Exchange SHOULD be called (fresh retry)
        exchange.place_limit_sell.assert_awaited_once()
        # Counter should be cleared on success
        assert state._store.get("sell_retry_count:42") is None

    @pytest.mark.asyncio
    async def test_retry_keys_cleared_on_terminal_status(self):
        """Retry keys are cleaned up when sell order reaches terminal status."""
        strategy = _make_sell_strategy()
        ctx = _make_ctx()
        state = _make_state()
        state._store["sell_retry_count:42"] = "2"
        state._store["sell_retry_after:42"] = str(9999.0)
        repos = _make_repos()
        exchange = AsyncMock()
        account_state = AsyncMock()
        lot = _make_lot(lot_id=42, sell_order_id=99999)

        for status in ["FILLED", "CANCELED", "REJECTED", "EXPIRED"]:
            # Reset state for each iteration
            state._store["sell_retry_count:42"] = "2"
            state._store["sell_retry_after:42"] = str(9999.0)

            if status == "FILLED":
                exchange.get_order = AsyncMock(
                    return_value={
                        "orderId": 99999,
                        "status": status,
                        "executedQty": "0.01",
                        "cummulativeQuoteQty": "20.66",
                        "updateTime": 1700000000000,
                    }
                )
                repos.lot.close_lot = AsyncMock()
                account_state.add_pending_earnings = AsyncMock()
            else:
                exchange.get_order = AsyncMock(
                    return_value={
                        "orderId": 99999,
                        "status": status,
                    }
                )
                repos.lot.clear_sell_order = AsyncMock()

            repos.order.upsert_order = AsyncMock()

            await strategy._check_existing_sell_order(
                ctx,
                state,
                exchange,
                account_state,
                repos,
                lot,
                2066.0,
                "always",
            )

            assert state._store.get("sell_retry_count:42") is None, f"sell_retry_count not cleared for status {status}"
            assert state._store.get("sell_retry_after:42") is None, f"sell_retry_after not cleared for status {status}"


# ===========================================================================
# Task 3: Orphaned order recovery
# ===========================================================================


@pytest.mark.unit
class TestOrphanRecovery:
    @pytest.mark.asyncio
    async def test_orphan_recovery_matches_client_order_id(self):
        """Orphan order with _TP_{lot_id} pattern is linked to the lot."""
        from app.services.account_trader import AccountTrader

        account_id = uuid4()

        # Mock the session with query results
        session = AsyncMock()

        # Mock orphan lot query result
        orphan_lot_row = MagicMock()
        orphan_lot_row.__getitem__ = lambda self, idx: 42
        orphan_lot_result = MagicMock()
        orphan_lot_result.all.return_value = [orphan_lot_row]

        # Mock sell order query result
        sell_order_result = MagicMock()
        sell_order_result.all.return_value = [
            (12345, "CMT_a1b2c3d4_e5f6a7b8__TP_42", 1700000000000),
        ]

        session.execute = AsyncMock(side_effect=[orphan_lot_result, sell_order_result])
        session.flush = AsyncMock()

        order_repo = AsyncMock()
        lot_repo = AsyncMock()
        lot_repo.set_sell_order = AsyncMock()

        trader = AccountTrader.__new__(AccountTrader)
        trader.account_id = account_id

        result = await trader._reconcile_orphan_sells(order_repo, lot_repo, session)

        assert result == 1
        lot_repo.set_sell_order.assert_awaited_once()
        call_kwargs = lot_repo.set_sell_order.call_args.kwargs
        assert call_kwargs["lot_id"] == 42
        assert call_kwargs["sell_order_id"] == 12345

    @pytest.mark.asyncio
    async def test_orphan_recovery_no_match_returns_zero(self):
        """No orphan lots → returns 0."""
        from app.services.account_trader import AccountTrader

        account_id = uuid4()
        session = AsyncMock()

        # No orphan lots
        orphan_lot_result = MagicMock()
        orphan_lot_result.all.return_value = []
        session.execute = AsyncMock(return_value=orphan_lot_result)

        order_repo = AsyncMock()
        lot_repo = AsyncMock()

        trader = AccountTrader.__new__(AccountTrader)
        trader.account_id = account_id

        result = await trader._reconcile_orphan_sells(order_repo, lot_repo, session)

        assert result == 0
        lot_repo.set_sell_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_orphan_recovery_ignores_non_tp_orders(self):
        """Orders without _TP_ pattern are ignored."""
        from app.services.account_trader import AccountTrader

        account_id = uuid4()
        session = AsyncMock()

        orphan_lot_row = MagicMock()
        orphan_lot_row.__getitem__ = lambda self, idx: 42
        orphan_lot_result = MagicMock()
        orphan_lot_result.all.return_value = [orphan_lot_row]

        # Order has a buy pattern, not TP
        sell_order_result = MagicMock()
        sell_order_result.all.return_value = [
            (12345, "CMT_abc_def__LOT_42", 1700000000000),
        ]

        session.execute = AsyncMock(side_effect=[orphan_lot_result, sell_order_result])

        order_repo = AsyncMock()
        lot_repo = AsyncMock()

        trader = AccountTrader.__new__(AccountTrader)
        trader.account_id = account_id

        result = await trader._reconcile_orphan_sells(order_repo, lot_repo, session)

        assert result == 0
        lot_repo.set_sell_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_orphan_recovery_ignores_already_linked_lots(self):
        """Orders matching a lot_id NOT in orphan set are ignored."""
        from app.services.account_trader import AccountTrader

        account_id = uuid4()
        session = AsyncMock()

        # Lot 42 has sell_order_id already set → NOT in orphan set
        # Only lot 99 is orphaned
        orphan_lot_row = MagicMock()
        orphan_lot_row.__getitem__ = lambda self, idx: 99
        orphan_lot_result = MagicMock()
        orphan_lot_result.all.return_value = [orphan_lot_row]

        # Order references lot 42 (not orphaned)
        sell_order_result = MagicMock()
        sell_order_result.all.return_value = [
            (12345, "CMT_a1b2c3d4_e5f6a7b8__TP_42", 1700000000000),
        ]

        session.execute = AsyncMock(side_effect=[orphan_lot_result, sell_order_result])

        order_repo = AsyncMock()
        lot_repo = AsyncMock()

        trader = AccountTrader.__new__(AccountTrader)
        trader.account_id = account_id

        result = await trader._reconcile_orphan_sells(order_repo, lot_repo, session)

        assert result == 0
        lot_repo.set_sell_order.assert_not_awaited()
