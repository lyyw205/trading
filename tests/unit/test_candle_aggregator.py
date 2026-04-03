"""CandleAggregator.run_once() unit tests — no DB, pure mocks."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.candle_aggregator import CandleAggregator


def _make_session_cm(session: AsyncMock):
    """Return an async context manager that yields the given session mock."""

    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


def _make_trading_session_local(session: AsyncMock):
    """Return a callable that produces the async context manager each time."""
    cm = _make_session_cm(session)
    return MagicMock(return_value=cm())


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

_TIERS = [
    ("1m", "5m"),
    ("5m", "1h"),
    ("1h", "1d"),
]


@pytest.mark.unit
@pytest.mark.asyncio
class TestRunOnce:
    # ------------------------------------------------------------------
    # 1. No symbols → returns empty dict
    # ------------------------------------------------------------------

    async def test_run_once_no_symbols_returns_empty(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        calls = []

        @asynccontextmanager
        async def fake_session_local():
            calls.append("open")
            yield session

        with (
            patch("app.services.candle_aggregator.TradingSessionLocal", fake_session_local),
            patch("app.services.candle_aggregator.aggregate_candles") as mock_agg,
            patch("app.services.candle_aggregator.delete_old_candles") as mock_del,
        ):
            aggregator = CandleAggregator()
            result = await aggregator.run_once()

        assert result == {}
        mock_agg.assert_not_called()
        mock_del.assert_not_called()

    # ------------------------------------------------------------------
    # 2. Single symbol, 1m→5m tier only (patched _TIERS effectively via
    #    aggregate_candles side_effect returning 0 for other tiers)
    # ------------------------------------------------------------------

    async def test_run_once_single_tier_aggregation(self):
        symbols = ["BTCUSDT"]

        # Session for symbol-query
        query_session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = [(s,) for s in symbols]
        query_session.execute = AsyncMock(return_value=result_mock)

        # Session for each tier transaction
        tier_session = AsyncMock()

        sessions_iter = iter([query_session] + [tier_session] * 10)

        @asynccontextmanager
        async def fake_session_local():
            yield next(sessions_iter)

        with (
            patch("app.services.candle_aggregator.TradingSessionLocal", fake_session_local),
            patch(
                "app.services.candle_aggregator.aggregate_candles",
                new_callable=AsyncMock,
            ) as mock_agg,
            patch(
                "app.services.candle_aggregator.delete_old_candles",
                new_callable=AsyncMock,
            ) as mock_del,
        ):
            # Only 1m→5m returns aggregated rows; others return 0
            mock_agg.side_effect = [5, 0, 0]
            mock_del.return_value = 3

            aggregator = CandleAggregator()
            result = await aggregator.run_once()

        assert "BTCUSDT" in result
        assert "1m->5m" in result["BTCUSDT"]
        entry = result["BTCUSDT"]["1m->5m"]
        assert entry["aggregated"] == 5
        assert entry["deleted"] == 3

        # aggregate_candles called with correct keyword args for first tier
        first_call = mock_agg.call_args_list[0]
        assert first_call.kwargs["symbol"] == "BTCUSDT"
        assert first_call.kwargs["source_interval"] == "1m"
        assert first_call.kwargs["target_interval"] == "5m"
        assert first_call.kwargs["session"] is tier_session

        # delete_old_candles called once (only when aggregated > 0)
        mock_del.assert_called_once()
        del_call = mock_del.call_args
        assert del_call.kwargs["symbol"] == "BTCUSDT"
        assert del_call.kwargs["interval"] == "1m"

    # ------------------------------------------------------------------
    # 3. 2 symbols × 3 tiers = 6 aggregate_candles calls
    # ------------------------------------------------------------------

    async def test_run_once_multi_tier_all_symbols(self):
        symbols = ["BTCUSDT", "ETHUSDT"]

        query_session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = [(s,) for s in symbols]
        query_session.execute = AsyncMock(return_value=result_mock)

        tier_session = AsyncMock()
        session_pool = iter([query_session] + [tier_session] * 20)

        @asynccontextmanager
        async def fake_session_local():
            yield next(session_pool)

        with (
            patch("app.services.candle_aggregator.TradingSessionLocal", fake_session_local),
            patch(
                "app.services.candle_aggregator.aggregate_candles",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_agg,
            patch(
                "app.services.candle_aggregator.delete_old_candles",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_del,
        ):
            aggregator = CandleAggregator()
            await aggregator.run_once()

        # 2 symbols × 3 tiers = 6 calls
        assert mock_agg.call_count == 6
        # delete never called since aggregated == 0
        mock_del.assert_not_called()

        # Verify each symbol appears in calls
        called_symbols = {c.kwargs["symbol"] for c in mock_agg.call_args_list}
        assert called_symbols == {"BTCUSDT", "ETHUSDT"}

        # Verify all three source intervals appear
        called_sources = {c.kwargs["source_interval"] for c in mock_agg.call_args_list}
        assert called_sources == {"1m", "5m", "1h"}

    # ------------------------------------------------------------------
    # 4. One tier raises Exception → other tiers still processed
    # ------------------------------------------------------------------

    async def test_run_once_tier_error_isolated(self):
        symbols = ["BTCUSDT"]

        query_session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = [(s,) for s in symbols]
        query_session.execute = AsyncMock(return_value=result_mock)

        tier_session = AsyncMock()
        session_pool = iter([query_session] + [tier_session] * 10)

        @asynccontextmanager
        async def fake_session_local():
            yield next(session_pool)

        with (
            patch("app.services.candle_aggregator.TradingSessionLocal", fake_session_local),
            patch(
                "app.services.candle_aggregator.aggregate_candles",
                new_callable=AsyncMock,
            ) as mock_agg,
            patch(
                "app.services.candle_aggregator.delete_old_candles",
                new_callable=AsyncMock,
                return_value=0,
            ),
        ):
            # First tier raises, second and third succeed with 0
            mock_agg.side_effect = [Exception("DB timeout"), 0, 0]

            aggregator = CandleAggregator()
            result = await aggregator.run_once()

        # All 3 tiers attempted despite first failure
        assert mock_agg.call_count == 3

        # Failed tier does not appear in result; others processed but returned 0
        # so symbol_summary is empty → symbol not in result
        assert result == {}

    # ------------------------------------------------------------------
    # 5. aggregated == 0 → delete_old_candles NOT called
    # ------------------------------------------------------------------

    async def test_run_once_no_aggregation_no_delete(self):
        symbols = ["ETHUSDT"]

        query_session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = [(s,) for s in symbols]
        query_session.execute = AsyncMock(return_value=result_mock)

        tier_session = AsyncMock()
        session_pool = iter([query_session] + [tier_session] * 10)

        @asynccontextmanager
        async def fake_session_local():
            yield next(session_pool)

        with (
            patch("app.services.candle_aggregator.TradingSessionLocal", fake_session_local),
            patch(
                "app.services.candle_aggregator.aggregate_candles",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_agg,
            patch(
                "app.services.candle_aggregator.delete_old_candles",
                new_callable=AsyncMock,
            ) as mock_del,
        ):
            aggregator = CandleAggregator()
            result = await aggregator.run_once()

        mock_agg.assert_called()
        mock_del.assert_not_called()
        assert result == {}

    # ------------------------------------------------------------------
    # 6. Each symbol×tier gets its own session + commit
    # ------------------------------------------------------------------

    async def test_run_once_commits_per_symbol_tier(self):
        symbols = ["BTCUSDT", "ETHUSDT"]
        num_tiers = 3

        query_session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = [(s,) for s in symbols]
        query_session.execute = AsyncMock(return_value=result_mock)

        # Track each unique session opened for tiers
        tier_sessions = [AsyncMock() for _ in range(len(symbols) * num_tiers)]
        sessions_iter = iter([query_session] + tier_sessions)

        @asynccontextmanager
        async def fake_session_local():
            yield next(sessions_iter)

        with (
            patch("app.services.candle_aggregator.TradingSessionLocal", fake_session_local),
            patch(
                "app.services.candle_aggregator.aggregate_candles",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "app.services.candle_aggregator.delete_old_candles",
                new_callable=AsyncMock,
                return_value=0,
            ),
        ):
            aggregator = CandleAggregator()
            await aggregator.run_once()

        # Every tier session must have had commit() called exactly once
        for s in tier_sessions:
            s.commit.assert_awaited_once()

        # Query session (symbol lookup) must NOT have had commit() called
        query_session.commit.assert_not_awaited()
