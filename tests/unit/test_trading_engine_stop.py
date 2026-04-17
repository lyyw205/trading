"""TradingEngine.stop_account calls stop_async test."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@pytest.mark.unit
class TestTradingEngineStop:
    async def test_stop_account_calls_stop_async(self):
        """stop_account should call trader.stop_async() not trader.stop()."""
        mock_trader = MagicMock()
        mock_trader.stop_async = AsyncMock()

        account_id = uuid4()

        from app.services.trading_engine import TradingEngine

        with patch.object(TradingEngine, "__init__", lambda self, *a, **kw: None):
            engine = TradingEngine.__new__(TradingEngine)
            engine._traders = {account_id: mock_trader}
            import asyncio

            loop = asyncio.get_event_loop()
            future = loop.create_future()
            future.cancel()  # Mark as cancelled so contextlib.suppress(CancelledError) works
            engine._tasks = {account_id: future}
            engine._account_symbols = {account_id: set()}
            engine._kline_ws = AsyncMock()

            await engine.stop_account(account_id)
            mock_trader.stop_async.assert_awaited_once()
