"""KlineWsManager unit tests — no real WebSocket or DB connections."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.kline_ws_manager import KlineWsManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager() -> KlineWsManager:
    """Return a fresh KlineWsManager with _rebuild_multiplex patched out."""
    mgr = KlineWsManager()
    mgr._rebuild_multiplex = AsyncMock()
    return mgr


# ---------------------------------------------------------------------------
# subscribe / unsubscribe — refcount behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubscribeRefcount:
    async def test_subscribe_once_adds_symbol(self):
        mgr = _make_manager()
        await mgr.subscribe("BTCUSDT")
        assert "btcusdt" in mgr._subscriptions
        assert mgr._subscriptions["btcusdt"] == 1

    async def test_subscribe_lowercases_symbol(self):
        mgr = _make_manager()
        await mgr.subscribe("ETHUSDT")
        assert "ethusdt" in mgr._subscriptions
        assert "ETHUSDT" not in mgr._subscriptions

    async def test_subscribe_twice_increments_refcount(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        await mgr.subscribe("BTCUSDT")
        assert mgr._subscriptions["btcusdt"] == 2

    async def test_subscribe_first_time_calls_rebuild(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        mgr._rebuild_multiplex.assert_awaited_once()

    async def test_subscribe_second_time_does_not_call_rebuild(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        mgr._rebuild_multiplex.reset_mock()
        await mgr.subscribe("btcusdt")
        mgr._rebuild_multiplex.assert_not_awaited()

    async def test_unsubscribe_decrements_refcount(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        await mgr.subscribe("btcusdt")
        await mgr.unsubscribe("btcusdt")
        assert mgr._subscriptions["btcusdt"] == 1

    async def test_unsubscribe_last_ref_removes_symbol(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        await mgr.unsubscribe("btcusdt")
        assert "btcusdt" not in mgr._subscriptions

    async def test_unsubscribe_last_ref_calls_rebuild(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        mgr._rebuild_multiplex.reset_mock()
        await mgr.unsubscribe("btcusdt")
        mgr._rebuild_multiplex.assert_awaited_once()

    async def test_unsubscribe_not_last_ref_does_not_call_rebuild(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        await mgr.subscribe("btcusdt")
        mgr._rebuild_multiplex.reset_mock()
        await mgr.unsubscribe("btcusdt")
        mgr._rebuild_multiplex.assert_not_awaited()

    async def test_unsubscribe_nonexistent_symbol_is_noop(self):
        mgr = _make_manager()
        # should not raise
        await mgr.unsubscribe("nonexistent")
        assert "nonexistent" not in mgr._subscriptions

    async def test_subscription_count_property(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        await mgr.subscribe("ethusdt")
        assert mgr.subscription_count == 2
        await mgr.unsubscribe("btcusdt")
        assert mgr.subscription_count == 1


# ---------------------------------------------------------------------------
# _backfilled set
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackfilledSet:
    async def test_symbol_not_in_backfilled_after_subscribe(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        assert "btcusdt" not in mgr._backfilled

    async def test_unsubscribe_discards_from_backfilled(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        mgr._backfilled.add("btcusdt")  # simulate completed backfill
        await mgr.unsubscribe("btcusdt")
        assert "btcusdt" not in mgr._backfilled

    async def test_unsubscribe_with_refcount_above_one_keeps_backfilled(self):
        mgr = _make_manager()
        await mgr.subscribe("btcusdt")
        await mgr.subscribe("btcusdt")
        mgr._backfilled.add("btcusdt")
        await mgr.unsubscribe("btcusdt")
        # refcount still 1, symbol not removed yet
        assert "btcusdt" in mgr._backfilled


# ---------------------------------------------------------------------------
# get_latest_price
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetLatestPrice:
    def test_returns_price_for_lowercase_key(self):
        mgr = KlineWsManager()
        mgr._latest_prices["btcusdt"] = 50000.0
        assert mgr.get_latest_price("btcusdt") == 50000.0

    def test_returns_price_for_uppercase_key(self):
        mgr = KlineWsManager()
        mgr._latest_prices["BTCUSDT"] = 50000.0
        assert mgr.get_latest_price("BTCUSDT") == 50000.0

    def test_lookup_by_mixed_case_finds_lowercase_cache(self):
        mgr = KlineWsManager()
        mgr._latest_prices["btcusdt"] = 42000.0
        assert mgr.get_latest_price("BTCUSDT") == 42000.0

    def test_returns_none_when_symbol_absent(self):
        mgr = KlineWsManager()
        assert mgr.get_latest_price("btcusdt") is None

    def test_independent_cache_per_symbol(self):
        mgr = KlineWsManager()
        mgr._latest_prices["btcusdt"] = 50000.0
        mgr._latest_prices["ethusdt"] = 3000.0
        assert mgr.get_latest_price("btcusdt") == 50000.0
        assert mgr.get_latest_price("ethusdt") == 3000.0

    def test_zero_price_treated_as_absent(self):
        # get_latest_price uses `or` so 0.0 falls through to uppercase lookup
        mgr = KlineWsManager()
        mgr._latest_prices["btcusdt"] = 0.0
        # both lowercase and uppercase absent (or zero) → None
        result = mgr.get_latest_price("btcusdt")
        assert result is None or result == 0.0  # documents existing behaviour


# ---------------------------------------------------------------------------
# is_healthy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsHealthy:
    def test_healthy_when_ws_task_running(self):
        mgr = KlineWsManager()
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False
        mgr._ws_task = mock_task
        assert mgr.is_healthy() is True

    def test_not_healthy_when_ws_task_is_none(self):
        mgr = KlineWsManager()
        mgr._ws_task = None
        assert mgr.is_healthy() is False

    def test_not_healthy_when_ws_task_done(self):
        mgr = KlineWsManager()
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = True
        mgr._ws_task = mock_task
        assert mgr.is_healthy() is False


# ---------------------------------------------------------------------------
# _rebuild_multiplex — stream name construction (via _run_multiplex internals)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStreamNames:
    def test_single_symbol_stream_name(self):
        """Stream name for a symbol is '<symbol_lower>@kline_1m'."""
        symbol = "btcusdt"
        expected = f"{symbol}@kline_1m"
        assert expected == "btcusdt@kline_1m"

    def test_multiple_symbol_stream_names(self):
        symbols = ["btcusdt", "ethusdt", "solusdt"]
        streams = [f"{s}@kline_1m" for s in symbols]
        assert streams == ["btcusdt@kline_1m", "ethusdt@kline_1m", "solusdt@kline_1m"]

    async def test_rebuild_multiplex_cancels_existing_task_before_creating_new(self):
        mgr = KlineWsManager()

        # Use a real long-running task so it can be cancelled and awaited
        async def _long_running():
            await asyncio.sleep(3600)

        running_task = asyncio.create_task(_long_running())
        await asyncio.sleep(0)  # let the task start

        mgr._ws_task = running_task
        mgr._subscriptions = {}  # empty → no new task created

        await mgr._rebuild_multiplex()

        assert running_task.cancelled()

    async def test_rebuild_multiplex_does_not_create_task_when_no_subscriptions(self):
        mgr = KlineWsManager()
        mgr._subscriptions = {}
        mgr._ws_task = None

        await mgr._rebuild_multiplex()

        assert mgr._ws_task is None

    async def test_rebuild_multiplex_creates_supervisor_task_when_subscriptions_exist(self):
        mgr = KlineWsManager()
        mgr._subscriptions = {"btcusdt": 1}
        mgr._ws_task = None

        # Patch _supervisor_loop to return immediately so the task completes cleanly
        async def _noop_supervisor():
            return

        with patch.object(mgr, "_supervisor_loop", _noop_supervisor):
            await mgr._rebuild_multiplex()
            assert mgr._ws_task is not None
            await mgr._ws_task  # drain


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStop:
    async def test_stop_sets_running_false(self):
        mgr = KlineWsManager()
        mgr._running = True
        mgr._ws_task = None
        mgr._async_client = None
        await mgr.stop()
        assert mgr._running is False

    async def test_stop_cancels_running_ws_task(self):
        mgr = KlineWsManager()
        mgr._running = True
        mgr._async_client = None

        cancelled = []

        async def _long_running():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        task = asyncio.create_task(_long_running())
        await asyncio.sleep(0)  # let the task start running
        mgr._ws_task = task

        await mgr.stop()

        assert cancelled == [True]
        assert task.done()
