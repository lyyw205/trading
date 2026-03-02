"""Unit tests for AccountTrader._sync_orders_and_fills and related methods."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.account_trader import AccountTrader
from app.services.price_collector import PriceCollector


@pytest.fixture
def trader():
    account_id = uuid.uuid4()
    price_collector = MagicMock(spec=PriceCollector)
    price_collector.get_price = AsyncMock(return_value=50000.0)
    rate_limiter = MagicMock()
    rate_limiter.acquire = AsyncMock()
    encryption = MagicMock()
    encryption.decrypt = MagicMock(return_value="test-key")

    from app.exchange.backtest_client import BacktestClient

    client = BacktestClient(
        symbol="BTCUSDT",
        initial_balance_usdt=10000.0,
        initial_balance_btc=0.0,
    )
    client.set_price(50000.0)

    t = AccountTrader(
        account_id=account_id,
        price_collector=price_collector,
        rate_limiter=rate_limiter,
        encryption=encryption,
    )
    t._client = client
    return t


def _make_sync_deps():
    """Return (account, order_repo, position_repo, session) mocks for _sync_orders_and_fills."""
    account = MagicMock()
    account.symbol = "BTCUSDT"

    order_repo = MagicMock()
    order_repo.get_recent_open_orders = AsyncMock(return_value=[])
    order_repo.upsert_order = AsyncMock()
    order_repo.upsert_orders_batch = AsyncMock()
    order_repo.insert_fill = AsyncMock()
    order_repo.insert_fills_batch = AsyncMock()

    position_repo = MagicMock()
    position_repo.recompute_from_fills = AsyncMock()

    session = AsyncMock()
    return account, order_repo, position_repo, session


# ---------------------------------------------------------------------------
# _sync_orders_and_fills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_parallel_open_orders(trader):
    """get_open_orders must be called once per symbol when syncing open orders."""
    symbols = {"BTCUSDT", "ETHUSDT"}
    account, order_repo, position_repo, session = _make_sync_deps()

    trader._client = AsyncMock()
    trader._client.get_open_orders = AsyncMock(
        return_value=[{"orderId": 1, "status": "NEW"}]
    )
    trader._client.get_my_trades = AsyncMock(return_value=[])

    # rate_limiter is already an AsyncMock via fixture; just ensure acquire works
    trader._rate_limiter.acquire = AsyncMock()

    await trader._sync_orders_and_fills(
        account, symbols, order_repo, position_repo, session
    )

    assert trader._client.get_open_orders.call_count == len(symbols)
    called_syms = {call.args[0] for call in trader._client.get_open_orders.call_args_list}
    assert called_syms == symbols


@pytest.mark.asyncio
async def test_sync_parallel_fills(trader):
    """get_my_trades must be called once per symbol when syncing fills."""
    symbols = {"BTCUSDT", "ETHUSDT"}
    account, order_repo, position_repo, session = _make_sync_deps()

    trader._client = AsyncMock()
    trader._client.get_open_orders = AsyncMock(return_value=[])
    trader._client.get_my_trades = AsyncMock(return_value=[])
    trader._rate_limiter.acquire = AsyncMock()

    await trader._sync_orders_and_fills(
        account, symbols, order_repo, position_repo, session
    )

    assert trader._client.get_my_trades.call_count == len(symbols)
    called_syms = {call.args[0] for call in trader._client.get_my_trades.call_args_list}
    assert called_syms == symbols


@pytest.mark.asyncio
async def test_sync_error_isolation(trader, caplog):
    """A failure for one symbol must not prevent the other symbol from being synced."""
    import logging

    symbols = {"BTCUSDT", "ETHUSDT"}
    account, order_repo, position_repo, session = _make_sync_deps()

    def _open_orders_side_effect(sym):
        if sym == "ETHUSDT":
            raise RuntimeError("network error")
        return [{"orderId": 42, "status": "NEW"}]

    trader._client = AsyncMock()
    trader._client.get_open_orders = AsyncMock(side_effect=_open_orders_side_effect)
    trader._client.get_my_trades = AsyncMock(return_value=[])
    trader._rate_limiter.acquire = AsyncMock()

    with caplog.at_level(logging.WARNING):
        # Must not raise even though one symbol errors out
        await trader._sync_orders_and_fills(
            account, symbols, order_repo, position_repo, session
        )

    # Warning logged for the failing symbol
    assert any("ETHUSDT" in record.message for record in caplog.records)

    # The successful symbol's order was still batch-upserted
    assert order_repo.upsert_orders_batch.call_count >= 1
    batch_call = order_repo.upsert_orders_batch.call_args_list[0]
    assert batch_call.args[0] == trader.account_id


# ---------------------------------------------------------------------------
# health_status
# ---------------------------------------------------------------------------


def test_health_status(trader):
    """health_status() must return a dict with the expected keys and correct defaults."""
    status = trader.health_status()

    assert isinstance(status, dict)
    assert status["running"] is True
    assert status["consecutive_failures"] == 0
    assert status["last_success_at"] is None
    # buy_pause_state defaults to ACTIVE
    from app.models.account import BuyPauseState

    assert status["buy_pause_state"] == BuyPauseState.ACTIVE


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop(trader):
    """stop() must set _running to False."""
    assert trader._running is True
    trader.stop()
    assert trader._running is False
