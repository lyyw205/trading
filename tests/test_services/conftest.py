"""Service-layer test fixtures."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.exchange.backtest_client import BacktestClient


@pytest.fixture
def buy_pause_manager_deps():
    """Common dependencies for BuyPauseManager tests."""
    return {
        "account_id": uuid.uuid4(),
    }


@pytest.fixture
def mock_rate_limiter():
    """GlobalRateLimiter stub that never blocks."""
    rl = MagicMock()
    rl.acquire = AsyncMock(return_value=None)
    return rl


@pytest.fixture
def mock_backtest_client():
    """BacktestClient pre-seeded with a price for service-layer tests."""
    client = BacktestClient(
        symbol="BTCUSDT",
        initial_balance_usdt=10_000.0,
        initial_balance_btc=0.0,
    )
    client.set_price(50_000.0)
    return client


@pytest_asyncio.fixture
async def account_trader(mock_rate_limiter, mock_backtest_client):
    """
    AccountTrader with injected BacktestClient.

    The trader is constructed with mocked collaborators so it never
    touches the real Binance API or the production DB session factory.
    """
    from unittest.mock import patch

    from app.services.account_trader import AccountTrader
    from app.services.price_collector import PriceCollector

    account_id = uuid.uuid4()
    price_collector = MagicMock(spec=PriceCollector)
    price_collector.get_price = AsyncMock(return_value=50_000.0)

    encryption = MagicMock()
    encryption.decrypt = MagicMock(return_value="test-key")

    # Patch BinanceClient construction so the trader uses our BacktestClient
    with patch(
        "app.services.account_trader.BinanceClient",
        return_value=mock_backtest_client,
    ):
        trader = AccountTrader(
            account_id=account_id,
            price_collector=price_collector,
            rate_limiter=mock_rate_limiter,
            encryption=encryption,
        )
        # Expose the injected client for assertion in tests
        trader._client = mock_backtest_client
        yield trader
