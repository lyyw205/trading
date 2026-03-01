"""FaultyBacktestClient failure-injection tests."""
import pytest

from app.exchange.faulty_backtest_client import FaultyBacktestClient

SYMBOL = "BTCUSDT"


@pytest.fixture
def faulty(request) -> FaultyBacktestClient:
    """Default faulty client: fails get_price after 2 calls."""
    return FaultyBacktestClient(
        symbol=SYMBOL,
        initial_balance_usdt=10000.0,
        initial_balance_btc=1.0,
        fail_after=2,
        fail_with=ConnectionError,
        fail_on_methods=["get_price"],
    )


# ---------------------------------------------------------------------------
# Normal behaviour before threshold
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestNormalBehaviourBeforeThreshold:
    async def test_get_price_works_within_fail_after(self, faulty):
        faulty.set_price(50000.0)
        # First two calls should succeed
        assert await faulty.get_price(SYMBOL) == 50000.0
        assert await faulty.get_price(SYMBOL) == 50000.0

    async def test_balance_works_without_being_in_fail_methods(self, faulty):
        # get_balance is NOT in fail_on_methods → always works
        bal = await faulty.get_balance("USDT")
        assert bal["free"] == 10000.0

    async def test_open_orders_not_in_fail_methods(self, faulty):
        orders = await faulty.get_open_orders(SYMBOL)
        assert orders == []

    async def test_place_buy_works_before_threshold_when_not_targeted(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            initial_balance_usdt=10000.0,
            fail_after=5,
            fail_with=ConnectionError,
            fail_on_methods=["get_price"],  # buy not targeted
        )
        client.set_price(50000.0)
        order = await client.place_limit_buy_by_quote(
            quote_usdt=500.0, price=50000.0, symbol=SYMBOL
        )
        assert order["status"] == "FILLED"


# ---------------------------------------------------------------------------
# Raises configured exception after threshold
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestRaisesAfterThreshold:
    async def test_get_price_raises_after_fail_after_calls(self, faulty):
        faulty.set_price(50000.0)
        # Consume the two allowed calls
        await faulty.get_price(SYMBOL)
        await faulty.get_price(SYMBOL)
        # Third call should raise
        with pytest.raises(ConnectionError, match="Simulated exchange failure"):
            await faulty.get_price(SYMBOL)

    async def test_raises_with_custom_message(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            fail_after=1,
            fail_with=ConnectionError,
            fail_message="Network timeout",
            fail_on_methods=["get_price"],
        )
        client.set_price(1.0)
        await client.get_price(SYMBOL)
        with pytest.raises(ConnectionError, match="Network timeout"):
            await client.get_price(SYMBOL)

    async def test_place_buy_raises_after_threshold(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            initial_balance_usdt=10000.0,
            fail_after=1,
            fail_with=ConnectionError,
            fail_on_methods=["place_limit_buy_by_quote"],
        )
        client.set_price(50000.0)
        # First call succeeds
        await client.place_limit_buy_by_quote(
            quote_usdt=100.0, price=50000.0, symbol=SYMBOL
        )
        with pytest.raises(ConnectionError):
            await client.place_limit_buy_by_quote(
                quote_usdt=100.0, price=50000.0, symbol=SYMBOL
            )

    async def test_place_sell_raises_after_threshold(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            initial_balance_usdt=10000.0,
            initial_balance_btc=1.0,
            fail_after=1,
            fail_with=ConnectionError,
            fail_on_methods=["place_limit_sell"],
        )
        client.set_price(50000.0)
        await client.place_limit_sell(qty_base=0.1, price=50000.0, symbol=SYMBOL)
        with pytest.raises(ConnectionError):
            await client.place_limit_sell(qty_base=0.1, price=50000.0, symbol=SYMBOL)

    async def test_keeps_raising_on_every_subsequent_call(self, faulty):
        faulty.set_price(1.0)
        await faulty.get_price(SYMBOL)
        await faulty.get_price(SYMBOL)
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await faulty.get_price(SYMBOL)


# ---------------------------------------------------------------------------
# Only fails on configured methods
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestOnlyFailsOnConfiguredMethods:
    async def test_non_targeted_methods_never_fail(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            initial_balance_usdt=10000.0,
            initial_balance_btc=1.0,
            fail_after=1,
            fail_with=ConnectionError,
            fail_on_methods=["get_price"],  # only get_price targeted
        )
        client.set_price(50000.0)
        # Exhaust the threshold
        await client.get_price(SYMBOL)

        # These should still work regardless
        bal = await client.get_balance("USDT")
        assert bal["free"] == pytest.approx(10000.0)

        orders = await client.get_open_orders(SYMBOL)
        assert isinstance(orders, list)

        free = await client.get_free_balance("BTC")
        assert free == pytest.approx(1.0)

    async def test_targeted_method_fails_non_targeted_succeeds(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            initial_balance_usdt=10000.0,
            fail_after=0,  # fails on EVERY call
            fail_with=ConnectionError,
            fail_on_methods=["place_limit_buy_by_quote"],
        )
        client.set_price(50000.0)

        # get_price should be fine
        price = await client.get_price(SYMBOL)
        assert price == 50000.0

        # place_limit_buy_by_quote should fail immediately
        with pytest.raises(ConnectionError):
            await client.place_limit_buy_by_quote(
                quote_usdt=100.0, price=50000.0, symbol=SYMBOL
            )

    async def test_each_method_has_independent_call_count(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            initial_balance_usdt=10000.0,
            initial_balance_btc=1.0,
            fail_after=2,
            fail_with=ConnectionError,
            fail_on_methods=["get_price", "place_limit_sell"],
        )
        client.set_price(50000.0)
        # Use up get_price allowance
        await client.get_price(SYMBOL)
        await client.get_price(SYMBOL)
        with pytest.raises(ConnectionError):
            await client.get_price(SYMBOL)

        # place_limit_sell still has its own independent counter (2 remaining)
        await client.place_limit_sell(qty_base=0.1, price=55000.0, symbol=SYMBOL)
        await client.place_limit_sell(qty_base=0.1, price=55000.0, symbol=SYMBOL)
        with pytest.raises(ConnectionError):
            await client.place_limit_sell(qty_base=0.1, price=55000.0, symbol=SYMBOL)


# ---------------------------------------------------------------------------
# reset_failures
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestResetFailures:
    async def test_reset_clears_call_counts(self, faulty):
        faulty.set_price(50000.0)
        await faulty.get_price(SYMBOL)
        await faulty.get_price(SYMBOL)
        # Would raise next call
        with pytest.raises(ConnectionError):
            await faulty.get_price(SYMBOL)

        # After reset, counter is cleared → 2 allowed calls again
        faulty.reset_failures()
        assert await faulty.get_price(SYMBOL) == 50000.0
        assert await faulty.get_price(SYMBOL) == 50000.0

    async def test_reset_allows_full_cycle_again(self, faulty):
        faulty.set_price(1.0)
        for _ in range(2):
            await faulty.get_price(SYMBOL)
        faulty.reset_failures()
        # Full allowance restored
        for _ in range(2):
            price = await faulty.get_price(SYMBOL)
            assert price == 1.0

    async def test_reset_does_not_change_fail_after_config(self, faulty):
        """reset_failures() only clears counts, not the configuration."""
        faulty.set_price(1.0)
        faulty.reset_failures()
        # Still has fail_after=2 → 3rd call should still fail
        await faulty.get_price(SYMBOL)
        await faulty.get_price(SYMBOL)
        with pytest.raises(ConnectionError):
            await faulty.get_price(SYMBOL)


# ---------------------------------------------------------------------------
# Different exception types
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestDifferentExceptionTypes:
    async def test_connection_error(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            fail_after=0,
            fail_with=ConnectionError,
            fail_on_methods=["get_price"],
        )
        client.set_price(1.0)
        with pytest.raises(ConnectionError):
            await client.get_price(SYMBOL)

    async def test_timeout_error(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            fail_after=0,
            fail_with=TimeoutError,
            fail_on_methods=["get_price"],
        )
        client.set_price(1.0)
        with pytest.raises(TimeoutError):
            await client.get_price(SYMBOL)

    async def test_value_error(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            fail_after=0,
            fail_with=ValueError,
            fail_message="Bad value from exchange",
            fail_on_methods=["get_price"],
        )
        client.set_price(1.0)
        with pytest.raises(ValueError, match="Bad value from exchange"):
            await client.get_price(SYMBOL)

    async def test_os_error(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            fail_after=1,
            fail_with=OSError,
            fail_on_methods=["get_price"],
        )
        client.set_price(1.0)
        await client.get_price(SYMBOL)  # first call ok
        with pytest.raises(OSError):
            await client.get_price(SYMBOL)

    async def test_runtime_error_on_place_buy(self):
        client = FaultyBacktestClient(
            symbol=SYMBOL,
            initial_balance_usdt=10000.0,
            fail_after=0,
            fail_with=RuntimeError,
            fail_message="Exchange refused",
            fail_on_methods=["place_limit_buy_by_quote"],
        )
        client.set_price(50000.0)
        with pytest.raises(RuntimeError, match="Exchange refused"):
            await client.place_limit_buy_by_quote(
                quote_usdt=100.0, price=50000.0, symbol=SYMBOL
            )
