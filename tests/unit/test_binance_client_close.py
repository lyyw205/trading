"""BinanceClient.close() credential clearing test."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestBinanceClientClose:
    async def test_close_clears_api_keys(self):
        """close() sets API_KEY and API_SECRET to empty strings."""
        with patch("app.exchange.binance_client.binance.client.Client") as MockClient:
            mock_instance = MagicMock()
            mock_instance.API_KEY = "real-key"
            mock_instance.API_SECRET = "real-secret"
            mock_instance.get_server_time.return_value = {"serverTime": 1000}
            MockClient.return_value = mock_instance

            from app.exchange.binance_client import BinanceClient

            client = BinanceClient("real-key", "real-secret", "BTCUSDT")

            await client.close()
            assert client.client.API_KEY == ""
            assert client.client.API_SECRET == ""
