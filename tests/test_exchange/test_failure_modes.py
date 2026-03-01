"""
Failure mode tests using FaultyBacktestClient (Tier 3).

Covers:
- Partial fill handling and lot state consistency
- Exchange API timeout during order operations
- Zero/stale price scenario — buy/sell guards
- Circuit breaker trigger path (5 consecutive failures -> disabled)
- Duplicate order prevention
"""
from __future__ import annotations

import contextlib

import pytest

from app.exchange.backtest_client import BacktestClient
from app.exchange.faulty_backtest_client import FaultyBacktestClient

SYMBOL = "BTCUSDT"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _faulty(
    *,
    fail_after: int = 0,
    fail_with: type[Exception] = TimeoutError,
    fail_on_methods: list[str] | None = None,
    initial_usdt: float = 10_000.0,
    initial_btc: float = 0.0,
) -> FaultyBacktestClient:
    return FaultyBacktestClient(
        symbol=SYMBOL,
        initial_balance_usdt=initial_usdt,
        initial_balance_btc=initial_btc,
        fail_after=fail_after,
        fail_with=fail_with,
        fail_message="Simulated exchange failure",
        fail_on_methods=fail_on_methods or ["get_price"],
    )


# ---------------------------------------------------------------------------
# Partial fill handling
# ---------------------------------------------------------------------------


@pytest.mark.exchange
class TestPartialFillHandling:
    """
    BacktestClient fills orders atomically (full or nothing), so we verify
    that balance accounting stays consistent when an order is partially set
    up then aborted, and that the books are clean after cancellation.
    """

    async def test_cancelled_buy_returns_full_locked_usdt(self):
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=0.0
        )
        client.set_price(60_000.0)  # above order price → stays open
        order = await client.place_limit_buy_by_quote(
            quote_usdt=1_000.0, price=50_000.0, symbol=SYMBOL
        )
        locked_after_place = (await client.get_balance("USDT"))["locked"]
        assert locked_after_place > 0, "USDT must be locked after placing buy"

        await client.cancel_order(order["orderId"], SYMBOL)

        bal = await client.get_balance("USDT")
        assert bal["locked"] == pytest.approx(0.0, abs=1e-6), (
            "All locked USDT must be released after cancel"
        )
        assert bal["free"] == pytest.approx(10_000.0, rel=1e-6), (
            "Free USDT must be fully restored after cancel"
        )

    async def test_cancelled_sell_returns_full_locked_btc(self):
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=1.0
        )
        client.set_price(40_000.0)  # below sell price → stays open
        order = await client.place_limit_sell(
            qty_base=0.5, price=55_000.0, symbol=SYMBOL
        )
        locked_btc = (await client.get_balance("BTC"))["locked"]
        assert locked_btc > 0, "BTC must be locked after placing sell"

        await client.cancel_order(order["orderId"], SYMBOL)

        bal = await client.get_balance("BTC")
        assert bal["locked"] == pytest.approx(0.0, abs=1e-8)
        assert bal["free"] == pytest.approx(1.0, rel=1e-4)

    async def test_open_orders_empty_after_both_orders_cancelled(self):
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=1.0
        )
        client.set_price(50_000.0)
        buy_order = await client.place_limit_buy_by_quote(
            quote_usdt=500.0, price=40_000.0, symbol=SYMBOL
        )
        sell_order = await client.place_limit_sell(
            qty_base=0.1, price=60_000.0, symbol=SYMBOL
        )
        assert len(await client.get_open_orders(SYMBOL)) == 2

        await client.cancel_order(buy_order["orderId"], SYMBOL)
        await client.cancel_order(sell_order["orderId"], SYMBOL)

        assert await client.get_open_orders(SYMBOL) == []

    async def test_fill_does_not_leave_residual_locked_funds(self):
        """After a buy fills, USDT locked must return to 0."""
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=0.0
        )
        client.set_price(50_000.0)
        await client.place_limit_buy_by_quote(
            quote_usdt=1_000.0, price=50_000.0, symbol=SYMBOL
        )
        # Price at order level → fills immediately
        bal = await client.get_balance("USDT")
        assert bal["locked"] == pytest.approx(0.0, abs=1e-6), (
            "No USDT should remain locked after fill"
        )
        assert await client.get_free_balance("BTC") > 0


# ---------------------------------------------------------------------------
# Exchange API timeout during order operations
# ---------------------------------------------------------------------------


@pytest.mark.exchange
class TestTimeoutDuringOrderOperations:
    async def test_timeout_on_place_buy_raises_timeout_error(self):
        client = _faulty(
            fail_after=0,
            fail_with=TimeoutError,
            fail_on_methods=["place_limit_buy_by_quote"],
            initial_usdt=10_000.0,
        )
        client.set_price(50_000.0)
        with pytest.raises(TimeoutError):
            await client.place_limit_buy_by_quote(
                quote_usdt=500.0, price=50_000.0, symbol=SYMBOL
            )

    async def test_timeout_on_place_sell_raises_timeout_error(self):
        client = _faulty(
            fail_after=0,
            fail_with=TimeoutError,
            fail_on_methods=["place_limit_sell"],
            initial_usdt=0.0,
            initial_btc=1.0,
        )
        client.set_price(50_000.0)
        with pytest.raises(TimeoutError):
            await client.place_limit_sell(
                qty_base=0.1, price=55_000.0, symbol=SYMBOL
            )

    async def test_balance_unchanged_when_buy_times_out(self):
        """A timed-out buy must not deduct any USDT."""
        client = _faulty(
            fail_after=0,
            fail_with=TimeoutError,
            fail_on_methods=["place_limit_buy_by_quote"],
            initial_usdt=10_000.0,
        )
        client.set_price(50_000.0)
        with contextlib.suppress(TimeoutError):
            await client.place_limit_buy_by_quote(
                quote_usdt=500.0, price=50_000.0, symbol=SYMBOL
            )

        bal = await client.get_balance("USDT")
        assert bal["free"] == pytest.approx(10_000.0), (
            "USDT must be unchanged when buy order timed out before placement"
        )

    async def test_graceful_degradation_allows_price_query_after_buy_timeout(self):
        """get_price still works even after place_limit_buy_by_quote fails."""
        client = _faulty(
            fail_after=0,
            fail_with=TimeoutError,
            fail_on_methods=["place_limit_buy_by_quote"],
            initial_usdt=10_000.0,
        )
        client.set_price(45_000.0)
        with contextlib.suppress(TimeoutError):
            await client.place_limit_buy_by_quote(
                quote_usdt=100.0, price=45_000.0, symbol=SYMBOL
            )

        price = await client.get_price(SYMBOL)
        assert price == pytest.approx(45_000.0)

    async def test_connection_error_on_get_order(self):
        client = _faulty(
            fail_after=0,
            fail_with=ConnectionError,
            fail_on_methods=["get_order"],
        )
        with pytest.raises(ConnectionError):
            await client.get_order(1, SYMBOL)


# ---------------------------------------------------------------------------
# Zero / stale price guards
# ---------------------------------------------------------------------------


@pytest.mark.exchange
class TestZeroStalePriceGuards:
    async def test_place_buy_raises_on_zero_price(self):
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=0.0
        )
        client.set_price(0.0)
        with pytest.raises(ValueError, match="price must be > 0"):
            await client.place_limit_buy_by_quote(
                quote_usdt=100.0, price=0.0, symbol=SYMBOL
            )

    async def test_place_sell_raises_on_zero_price(self):
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=0.0, initial_balance_btc=1.0
        )
        client.set_price(0.0)
        with pytest.raises(ValueError, match="price must be > 0"):
            await client.place_limit_sell(
                qty_base=0.1, price=0.0, symbol=SYMBOL
            )

    async def test_place_buy_raises_on_negative_price(self):
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=0.0
        )
        client.set_price(50_000.0)
        with pytest.raises(ValueError):
            await client.place_limit_buy_by_quote(
                quote_usdt=100.0, price=-1.0, symbol=SYMBOL
            )

    async def test_get_price_returns_zero_when_stale(self):
        """Stale price scenario: no set_price called → returns 0.0."""
        client = BacktestClient(symbol=SYMBOL)
        price = await client.get_price(SYMBOL)
        assert price == 0.0

    async def test_place_buy_qty_zero_raises_when_price_very_high(self):
        """
        If price is so high that qty rounds down to 0, place_limit_buy_by_quote
        must raise rather than silently place a zero-qty order.
        """
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=0.0
        )
        client.set_price(1.0)
        # quote=0.000001 at price=1e10 → qty rounds to 0
        with pytest.raises(ValueError):
            await client.place_limit_buy_by_quote(
                quote_usdt=0.000001, price=1e10, symbol=SYMBOL
            )


# ---------------------------------------------------------------------------
# Circuit breaker trigger path
# ---------------------------------------------------------------------------


@pytest.mark.exchange
class TestCircuitBreakerTriggerPath:
    """
    The BacktestClient itself has no circuit breaker — that logic lives in
    AccountTrader.  These tests verify the failure-injection pattern that
    would drive a circuit breaker: 5 consecutive exceptions from the
    exchange client.
    """

    async def test_five_consecutive_failures_all_raise(self):
        client = _faulty(
            fail_after=0,
            fail_with=ConnectionError,
            fail_on_methods=["get_price"],
        )
        client.set_price(50_000.0)
        failures = 0
        for _ in range(5):
            try:
                await client.get_price(SYMBOL)
            except ConnectionError:
                failures += 1
        assert failures == 5, "All 5 calls must raise to trigger circuit breaker logic"

    async def test_failure_count_independent_per_method(self):
        """Each method tracks failures independently."""
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            initial_balance_usdt=10_000.0,
            initial_balance_btc=1.0,
            fail_after=4,
            fail_with=ConnectionError,
            fail_on_methods=["get_price", "place_limit_sell"],
        )
        client.set_price(50_000.0)
        # Exhaust get_price (4 allowed, 5th fails)
        for _ in range(4):
            await client.get_price(SYMBOL)
        with pytest.raises(ConnectionError):
            await client.get_price(SYMBOL)

        # place_limit_sell still has its own fresh counter
        for _ in range(4):
            await client.place_limit_sell(qty_base=0.01, price=55_000.0, symbol=SYMBOL)
        with pytest.raises(ConnectionError):
            await client.place_limit_sell(qty_base=0.01, price=55_000.0, symbol=SYMBOL)

    async def test_reset_restores_operation_after_circuit_trip(self):
        client = _faulty(
            fail_after=0,
            fail_with=ConnectionError,
            fail_on_methods=["get_price"],
        )
        client.set_price(30_000.0)
        for _ in range(5):
            with contextlib.suppress(ConnectionError):
                await client.get_price(SYMBOL)

        client.disable_failures()
        # Should work again after disabling failures
        price = await client.get_price(SYMBOL)
        assert price == pytest.approx(30_000.0)

    async def test_non_targeted_methods_unaffected_during_circuit_trip(self):
        client = _faulty(
            fail_after=0,
            fail_with=ConnectionError,
            fail_on_methods=["get_price"],
            initial_usdt=10_000.0,
        )
        client.set_price(50_000.0)
        # get_price is down
        with pytest.raises(ConnectionError):
            await client.get_price(SYMBOL)

        # get_balance is still operational
        bal = await client.get_balance("USDT")
        assert bal["free"] == pytest.approx(10_000.0)


# ---------------------------------------------------------------------------
# Duplicate order prevention
# ---------------------------------------------------------------------------


@pytest.mark.exchange
class TestDuplicateOrderPrevention:
    async def test_same_client_order_id_creates_distinct_exchange_order_ids(self):
        """
        BacktestClient assigns monotonically increasing orderId regardless of
        clientOrderId, so two orders with the same clientOrderId get different
        exchange IDs — callers must not assume idempotency.
        """
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=0.0
        )
        client.set_price(60_000.0)  # above both order prices → stays open

        o1 = await client.place_limit_buy_by_quote(
            quote_usdt=100.0, price=50_000.0, symbol=SYMBOL, client_oid="dup-oid"
        )
        o2 = await client.place_limit_buy_by_quote(
            quote_usdt=100.0, price=50_000.0, symbol=SYMBOL, client_oid="dup-oid"
        )
        assert o1["orderId"] != o2["orderId"], (
            "Each place_limit_buy_by_quote must produce a unique exchange orderId"
        )

    async def test_two_open_orders_tracked_independently(self):
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=0.0
        )
        client.set_price(60_000.0)
        o1 = await client.place_limit_buy_by_quote(
            quote_usdt=200.0, price=50_000.0, symbol=SYMBOL
        )
        o2 = await client.place_limit_buy_by_quote(
            quote_usdt=200.0, price=50_000.0, symbol=SYMBOL
        )
        open_orders = await client.get_open_orders(SYMBOL)
        ids = {o["orderId"] for o in open_orders}
        assert o1["orderId"] in ids
        assert o2["orderId"] in ids
        assert len(ids) == 2

    async def test_cancelling_one_order_leaves_sibling_open(self):
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=0.0
        )
        client.set_price(60_000.0)
        o1 = await client.place_limit_buy_by_quote(
            quote_usdt=200.0, price=50_000.0, symbol=SYMBOL
        )
        o2 = await client.place_limit_buy_by_quote(
            quote_usdt=200.0, price=50_000.0, symbol=SYMBOL
        )
        await client.cancel_order(o1["orderId"], SYMBOL)

        open_orders = await client.get_open_orders(SYMBOL)
        assert len(open_orders) == 1
        assert open_orders[0]["orderId"] == o2["orderId"]

    async def test_get_order_returns_not_found_for_already_cancelled(self):
        client = BacktestClient(
            symbol=SYMBOL, initial_balance_usdt=10_000.0, initial_balance_btc=0.0
        )
        client.set_price(60_000.0)
        order = await client.place_limit_buy_by_quote(
            quote_usdt=200.0, price=50_000.0, symbol=SYMBOL
        )
        await client.cancel_order(order["orderId"], SYMBOL)

        fetched = await client.get_order(order["orderId"], SYMBOL)
        # Cancelled orders are removed from open_orders and not added to
        # filled_orders, so get_order returns NOT_FOUND
        assert fetched["status"] == "NOT_FOUND"
