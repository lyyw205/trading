"""Unit tests for TradingEngine.refresh_subscriptions — symbol diff logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def _make_engine():
    """Create a TradingEngine with mocked dependencies (no I/O)."""
    with (
        patch("app.services.trading_engine.PriceCollector"),
        patch("app.services.trading_engine.KlineWsManager") as MockKlineWs,
    ):
        from app.services.trading_engine import TradingEngine

        mock_kline_ws = MockKlineWs.return_value
        mock_kline_ws.subscribe = AsyncMock()
        mock_kline_ws.unsubscribe = AsyncMock()
        mock_kline_ws.is_healthy = MagicMock(return_value=True)
        mock_kline_ws.subscription_count = 0

        engine = TradingEngine(
            rate_limiter=MagicMock(),
            encryption=MagicMock(),
        )
        return engine


class TestRefreshSubscriptions:
    async def test_new_symbols_subscribed(self):
        """New symbols trigger subscribe calls."""
        engine = _make_engine()
        account_id = uuid4()
        # Simulate running account
        engine._traders[account_id] = MagicMock()
        engine._account_symbols[account_id] = set()

        with patch.object(engine, "_get_combo_symbols", new=AsyncMock(return_value={"btcusdt", "ethusdt"})):
            await engine.refresh_subscriptions(account_id)

        assert engine._kline_ws.subscribe.call_count == 2
        subscribed = {c.args[0] for c in engine._kline_ws.subscribe.call_args_list}
        assert subscribed == {"btcusdt", "ethusdt"}
        assert engine._account_symbols[account_id] == {"btcusdt", "ethusdt"}

    async def test_removed_symbols_unsubscribed(self):
        """Removed symbols trigger unsubscribe calls."""
        engine = _make_engine()
        account_id = uuid4()
        engine._traders[account_id] = MagicMock()
        engine._account_symbols[account_id] = {"btcusdt", "ethusdt"}

        with patch.object(engine, "_get_combo_symbols", new=AsyncMock(return_value={"btcusdt"})):
            await engine.refresh_subscriptions(account_id)

        engine._kline_ws.unsubscribe.assert_called_once_with("ethusdt")
        engine._kline_ws.subscribe.assert_not_called()
        assert engine._account_symbols[account_id] == {"btcusdt"}

    async def test_no_change_no_calls(self):
        """When symbols are unchanged, no subscribe/unsubscribe calls."""
        engine = _make_engine()
        account_id = uuid4()
        engine._traders[account_id] = MagicMock()
        engine._account_symbols[account_id] = {"btcusdt"}

        with patch.object(engine, "_get_combo_symbols", new=AsyncMock(return_value={"btcusdt"})):
            await engine.refresh_subscriptions(account_id)

        engine._kline_ws.subscribe.assert_not_called()
        engine._kline_ws.unsubscribe.assert_not_called()

    async def test_inactive_account_skipped(self):
        """Account not in _traders returns immediately."""
        engine = _make_engine()
        account_id = uuid4()
        # Do NOT add to _traders

        with patch.object(engine, "_get_combo_symbols", new=AsyncMock()) as mock_get:
            await engine.refresh_subscriptions(account_id)

        mock_get.assert_not_called()
        engine._kline_ws.subscribe.assert_not_called()
        engine._kline_ws.unsubscribe.assert_not_called()
