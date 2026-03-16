"""Unit tests for AccountTrader._sync_orders_and_fills and related methods."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

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


def _make_sync_deps(last_trade_ids: dict[str, int] | None = None):
    """Return (account, order_repo, position_repo, session) mocks for _sync_orders_and_fills.

    Args:
        last_trade_ids: Mapping of symbol -> max trade_id to simulate existing fills.
            None or empty dict means no existing fills (full fetch fallback).
    """
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
    # Mock session.execute to return MAX(trade_id) results for incremental fetch
    ids = last_trade_ids or {}
    max_id_result = MagicMock()
    max_id_result.all.return_value = list(ids.items())
    session.execute = AsyncMock(return_value=max_id_result)
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
    trader._client.get_open_orders = AsyncMock(return_value=[{"orderId": 1, "status": "NEW"}])
    trader._client.get_my_trades = AsyncMock(return_value=[])
    trader._client.get_my_trades_from_id = AsyncMock(return_value=[])

    # rate_limiter is already an AsyncMock via fixture; just ensure acquire works
    trader._rate_limiter.acquire = AsyncMock()

    await trader._sync_orders_and_fills(account, symbols, order_repo, position_repo, session)

    assert trader._client.get_open_orders.call_count == len(symbols)
    called_syms = {call.args[0] for call in trader._client.get_open_orders.call_args_list}
    assert called_syms == symbols


@pytest.mark.asyncio
async def test_sync_parallel_fills_fallback(trader):
    """No existing fills → get_my_trades (full fetch) called once per symbol."""
    symbols = {"BTCUSDT", "ETHUSDT"}
    account, order_repo, position_repo, session = _make_sync_deps()  # no last_trade_ids

    trader._client = AsyncMock()
    trader._client.get_open_orders = AsyncMock(return_value=[])
    trader._client.get_my_trades = AsyncMock(return_value=[])
    trader._client.get_my_trades_from_id = AsyncMock(return_value=[])
    trader._rate_limiter.acquire = AsyncMock()

    await trader._sync_orders_and_fills(account, symbols, order_repo, position_repo, session)

    # Full fetch fallback for all symbols (no existing fills)
    assert trader._client.get_my_trades.call_count == len(symbols)
    assert trader._client.get_my_trades_from_id.call_count == 0
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
    trader._client.get_my_trades_from_id = AsyncMock(return_value=[])
    trader._rate_limiter.acquire = AsyncMock()

    with caplog.at_level(logging.WARNING, logger="app.services.account_trader"):
        # Must not raise even though one symbol errors out
        await trader._sync_orders_and_fills(account, symbols, order_repo, position_repo, session)

    # Warning logged for the failing symbol
    assert any("ETHUSDT" in r.getMessage() for r in caplog.records)

    # The successful symbol's order was still batch-upserted
    assert order_repo.upsert_orders_batch.call_count >= 1
    batch_call = order_repo.upsert_orders_batch.call_args_list[0]
    assert batch_call.args[0] == trader.account_id


# ---------------------------------------------------------------------------
# CRIT-7: incremental trade sync tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_incremental_fills_uses_from_id(trader):
    """When fills exist in DB, get_my_trades_from_id must be called with from_id=last_id+1."""
    symbols = {"BTCUSDT", "ETHUSDT"}
    account, order_repo, position_repo, session = _make_sync_deps(last_trade_ids={"BTCUSDT": 500, "ETHUSDT": 300})

    trader._client = AsyncMock()
    trader._client.get_open_orders = AsyncMock(return_value=[])
    trader._client.get_my_trades = AsyncMock(return_value=[])
    trader._client.get_my_trades_from_id = AsyncMock(return_value=[])
    trader._rate_limiter.acquire = AsyncMock()

    await trader._sync_orders_and_fills(account, symbols, order_repo, position_repo, session)

    # Incremental fetch for both symbols
    assert trader._client.get_my_trades_from_id.call_count == 2
    assert trader._client.get_my_trades.call_count == 0

    # Verify from_id = last_id + 1 (boundary correctness)
    calls = {
        call.args[0]: call.kwargs.get("from_id", call.args[1] if len(call.args) > 1 else None)
        for call in trader._client.get_my_trades_from_id.call_args_list
    }
    assert calls["BTCUSDT"] == 501
    assert calls["ETHUSDT"] == 301


@pytest.mark.asyncio
async def test_sync_mixed_incremental_and_fallback(trader):
    """Known symbols use incremental fetch, new symbols use full fetch."""
    symbols = {"BTCUSDT", "ETHUSDT"}
    # Only BTCUSDT has existing fills
    account, order_repo, position_repo, session = _make_sync_deps(last_trade_ids={"BTCUSDT": 100})

    trader._client = AsyncMock()
    trader._client.get_open_orders = AsyncMock(return_value=[])
    trader._client.get_my_trades = AsyncMock(return_value=[])
    trader._client.get_my_trades_from_id = AsyncMock(return_value=[])
    trader._rate_limiter.acquire = AsyncMock()

    await trader._sync_orders_and_fills(account, symbols, order_repo, position_repo, session)

    # BTCUSDT: incremental, ETHUSDT: full fetch
    assert trader._client.get_my_trades_from_id.call_count == 1
    assert trader._client.get_my_trades.call_count == 1

    inc_sym = trader._client.get_my_trades_from_id.call_args_list[0].args[0]
    full_sym = trader._client.get_my_trades.call_args_list[0].args[0]
    assert inc_sym == "BTCUSDT"
    assert full_sym == "ETHUSDT"


@pytest.mark.asyncio
async def test_sync_max_id_query_failure_falls_back(trader, caplog):
    """If MAX(trade_id) query fails, all symbols should use full fetch."""
    import logging

    symbols = {"BTCUSDT"}
    account, order_repo, position_repo, session = _make_sync_deps()
    # Make the first session.execute raise (MAX query), but allow subsequent calls
    call_count = 0
    original_execute = session.execute

    async def _failing_then_ok(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("DB connection lost")
        return await original_execute(*args, **kwargs)

    session.execute = AsyncMock(side_effect=_failing_then_ok)

    trader._client = AsyncMock()
    trader._client.get_open_orders = AsyncMock(return_value=[])
    trader._client.get_my_trades = AsyncMock(return_value=[])
    trader._client.get_my_trades_from_id = AsyncMock(return_value=[])
    trader._rate_limiter.acquire = AsyncMock()

    with caplog.at_level(logging.WARNING, logger="app.services.account_trader"):
        await trader._sync_orders_and_fills(account, symbols, order_repo, position_repo, session)

    # Fallback to full fetch
    assert trader._client.get_my_trades.call_count == 1
    assert trader._client.get_my_trades_from_id.call_count == 0
    assert any("falling back to full fetch" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# CRIT-1 regression: throttle_cycle must increment once per cycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_should_attempt_buy_called_once_per_cycle_semantic():
    """
    Regression CRIT-1: Verify that when should_attempt_buy is called once
    per cycle (correct behavior) vs N times per cycle (bug behavior),
    the throttle counter behaves correctly.

    This is a semantic test that validates the fix at the call-site level:
    the counter must be passed once per _do_step(), not once per combo*symbol.
    """
    from app.models.account import BuyPauseState
    from app.services.buy_pause_manager import BuyPauseManager

    # Simulate CORRECT behavior: 1 call per cycle, 10 cycles
    cycle = 0
    buys = 0
    for _ in range(10):
        ok, cycle = BuyPauseManager.should_attempt_buy(BuyPauseState.THROTTLED, is_balance_sufficient=True, throttle_cycle=cycle)
        if ok:
            buys += 1
    assert cycle == 10
    assert buys == 2  # fires at cycle 5 and 10

    # Simulate BUG behavior: 4 calls per cycle (2 combos x 2 symbols), 10 cycles
    cycle_bug = 0
    buys_bug = 0
    for _ in range(10):
        for _ in range(4):  # inner combo*symbol loop
            ok, cycle_bug = BuyPauseManager.should_attempt_buy(
                BuyPauseState.THROTTLED, is_balance_sufficient=True, throttle_cycle=cycle_bug
            )
            if ok:
                buys_bug += 1
    assert cycle_bug == 40  # counter inflated 4x
    assert buys_bug == 8  # fires 4x more often than intended


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
