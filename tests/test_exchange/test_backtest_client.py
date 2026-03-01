"""BacktestClient unit tests."""
import pytest

from app.exchange.backtest_client import BacktestClient

SYMBOL = "BTCUSDT"


@pytest.fixture
def client() -> BacktestClient:
    return BacktestClient(symbol=SYMBOL, initial_balance_usdt=10000.0, initial_balance_btc=0.0)


@pytest.fixture
def client_with_btc() -> BacktestClient:
    return BacktestClient(symbol=SYMBOL, initial_balance_usdt=10000.0, initial_balance_btc=1.0)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestInitialState:
    async def test_usdt_free_balance_matches_initial(self, client):
        bal = await client.get_balance("USDT")
        assert bal["free"] == 10000.0
        assert bal["locked"] == 0.0
        assert bal["total"] == 10000.0

    async def test_btc_balance_zero_when_not_provided(self, client):
        bal = await client.get_balance("BTC")
        assert bal["free"] == 0.0
        assert bal["locked"] == 0.0

    async def test_initial_btc_balance_when_provided(self, client_with_btc):
        assert await client_with_btc.get_free_balance("BTC") == 1.0

    async def test_no_open_orders_initially(self, client):
        orders = await client.get_open_orders(SYMBOL)
        assert orders == []

    async def test_no_trades_initially(self, client):
        trades = await client.get_my_trades(SYMBOL)
        assert trades == []


# ---------------------------------------------------------------------------
# Price
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestPrice:
    async def test_get_price_returns_set_price(self, client):
        client.set_price(50000.0)
        assert await client.get_price(SYMBOL) == 50000.0

    async def test_get_price_zero_initially(self, client):
        assert await client.get_price(SYMBOL) == 0.0

    async def test_set_price_updates_current_price(self, client):
        client.set_price(1234.56)
        client.set_price(9999.99)
        assert await client.get_price(SYMBOL) == 9999.99


# ---------------------------------------------------------------------------
# place_limit_buy_by_quote
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestPlaceLimitBuy:
    async def test_reduces_free_usdt_increases_locked(self, client):
        # current price 300 > order price 200 → order stays open (not filled)
        client.set_price(300.0)
        await client.place_limit_buy_by_quote(
            quote_usdt=100.0, price=200.0, symbol=SYMBOL
        )
        bal = await client.get_balance("USDT")
        # price=200, adjust_qty(100/200=0.5, step=0.00001)=0.49999, cost=0.49999*200=99.998
        adj_qty = 0.49999
        cost = adj_qty * 200.0
        assert bal["free"] == pytest.approx(10000.0 - cost, rel=1e-6)
        assert bal["locked"] == pytest.approx(cost, rel=1e-6)

    async def test_order_appears_in_open_orders(self, client):
        # current price 300 > order price 200 → order stays open
        client.set_price(300.0)
        await client.place_limit_buy_by_quote(
            quote_usdt=100.0, price=200.0, symbol=SYMBOL
        )
        orders = await client.get_open_orders(SYMBOL)
        assert len(orders) == 1
        assert orders[0]["side"] == "BUY"
        assert orders[0]["status"] == "NEW"

    async def test_order_fills_when_price_at_or_below_order_price(self, client):
        # Place buy at 200, then set current price at 200 (equal fills)
        client.set_price(300.0)
        await client.place_limit_buy_by_quote(
            quote_usdt=200.0, price=200.0, symbol=SYMBOL
        )
        # Now price drops to fill level
        client.set_price(200.0)
        orders = await client.get_open_orders(SYMBOL)
        assert len(orders) == 0  # filled
        btc_bal = await client.get_balance("BTC")
        assert btc_bal["free"] > 0

    async def test_immediate_fill_when_current_price_below_order_price(self, client):
        # Current price 50 < order price 100 → fills immediately on place
        client.set_price(50.0)
        await client.place_limit_buy_by_quote(
            quote_usdt=500.0, price=100.0, symbol=SYMBOL
        )
        orders = await client.get_open_orders(SYMBOL)
        assert len(orders) == 0
        assert await client.get_free_balance("BTC") > 0

    async def test_fill_increases_btc_balance(self, client):
        client.set_price(50000.0)
        # Buy 1000 USDT at 50000 → qty = 1000/50000 = 0.02 BTC
        await client.place_limit_buy_by_quote(
            quote_usdt=1000.0, price=50000.0, symbol=SYMBOL
        )
        # Set price equal to order price to fill
        client.set_price(50000.0)
        btc_free = await client.get_free_balance("BTC")
        assert btc_free == pytest.approx(0.02, rel=1e-3)

    async def test_raises_on_insufficient_usdt(self, client):
        client.set_price(100.0)
        with pytest.raises(ValueError, match="Insufficient USDT"):
            await client.place_limit_buy_by_quote(
                quote_usdt=20000.0, price=100.0, symbol=SYMBOL
            )

    async def test_raises_on_zero_price(self, client):
        with pytest.raises(ValueError, match="price must be > 0"):
            await client.place_limit_buy_by_quote(
                quote_usdt=100.0, price=0.0, symbol=SYMBOL
            )

    async def test_raises_on_negative_price(self, client):
        with pytest.raises(ValueError):
            await client.place_limit_buy_by_quote(
                quote_usdt=100.0, price=-1.0, symbol=SYMBOL
            )

    async def test_order_contains_expected_fields(self, client):
        client.set_price(100.0)
        order = await client.place_limit_buy_by_quote(
            quote_usdt=100.0, price=200.0, symbol=SYMBOL
        )
        for field in ("orderId", "symbol", "side", "type", "status", "price", "origQty"):
            assert field in order

    async def test_client_oid_stored_in_order(self, client):
        client.set_price(100.0)
        order = await client.place_limit_buy_by_quote(
            quote_usdt=100.0, price=200.0, symbol=SYMBOL, client_oid="my-oid-123"
        )
        assert order["clientOrderId"] == "my-oid-123"


# ---------------------------------------------------------------------------
# place_limit_sell
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestPlaceLimitSell:
    async def test_reduces_free_btc_increases_locked(self, client_with_btc):
        # current price 50000 < sell price 55000 → order stays open
        # adjust_qty(0.5, step_size=0.00001) = 0.49999 due to float floor
        client_with_btc.set_price(50000.0)
        await client_with_btc.place_limit_sell(
            qty_base=0.5, price=55000.0, symbol=SYMBOL
        )
        bal = await client_with_btc.get_balance("BTC")
        adj_qty = 0.49999
        assert bal["free"] == pytest.approx(1.0 - adj_qty, rel=1e-4)
        assert bal["locked"] == pytest.approx(adj_qty, rel=1e-4)

    async def test_order_appears_in_open_orders(self, client_with_btc):
        client_with_btc.set_price(50000.0)
        await client_with_btc.place_limit_sell(
            qty_base=0.1, price=55000.0, symbol=SYMBOL
        )
        orders = await client_with_btc.get_open_orders(SYMBOL)
        assert len(orders) == 1
        assert orders[0]["side"] == "SELL"

    async def test_fills_when_price_reaches_sell_price(self, client_with_btc):
        client_with_btc.set_price(40000.0)
        await client_with_btc.place_limit_sell(
            qty_base=1.0, price=50000.0, symbol=SYMBOL
        )
        client_with_btc.set_price(50000.0)
        orders = await client_with_btc.get_open_orders(SYMBOL)
        assert len(orders) == 0

    async def test_fill_increases_usdt_balance(self, client_with_btc):
        # adjust_qty(1.0, step_size=0.00001) = 0.99999; proceeds = 0.99999 * 50000
        initial_usdt = await client_with_btc.get_free_balance("USDT")
        client_with_btc.set_price(40000.0)
        order = await client_with_btc.place_limit_sell(
            qty_base=1.0, price=50000.0, symbol=SYMBOL
        )
        adj_qty = float(order["origQty"])
        client_with_btc.set_price(50000.0)
        final_usdt = await client_with_btc.get_free_balance("USDT")
        assert final_usdt == pytest.approx(initial_usdt + adj_qty * 50000.0, rel=1e-6)

    async def test_raises_on_insufficient_btc(self, client):
        client.set_price(50000.0)
        with pytest.raises(ValueError, match="Insufficient BTC"):
            await client.place_limit_sell(
                qty_base=1.0, price=50000.0, symbol=SYMBOL
            )

    async def test_raises_on_zero_price(self, client_with_btc):
        with pytest.raises(ValueError, match="price must be > 0"):
            await client_with_btc.place_limit_sell(
                qty_base=0.1, price=0.0, symbol=SYMBOL
            )

    async def test_immediate_fill_when_current_price_above_order_price(
        self, client_with_btc
    ):
        # Current price 60000 > order sell price 50000 → fills immediately
        client_with_btc.set_price(60000.0)
        await client_with_btc.place_limit_sell(
            qty_base=1.0, price=50000.0, symbol=SYMBOL
        )
        orders = await client_with_btc.get_open_orders(SYMBOL)
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestCancelOrder:
    async def test_cancel_returns_locked_usdt_to_free(self, client):
        # current price 300 > order price 200 → order stays open (not filled)
        client.set_price(300.0)
        order = await client.place_limit_buy_by_quote(
            quote_usdt=1000.0, price=200.0, symbol=SYMBOL
        )
        order_id = order["orderId"]
        free_before = await client.get_free_balance("USDT")
        locked_before = (await client.get_balance("USDT"))["locked"]

        await client.cancel_order(order_id, SYMBOL)

        free_after = await client.get_free_balance("USDT")
        locked_after = (await client.get_balance("USDT"))["locked"]
        assert free_after > free_before
        assert locked_after < locked_before

    async def test_cancel_removes_order_from_open_orders(self, client):
        # current price 300 > order price 200 → order stays open
        client.set_price(300.0)
        order = await client.place_limit_buy_by_quote(
            quote_usdt=500.0, price=200.0, symbol=SYMBOL
        )
        await client.cancel_order(order["orderId"], SYMBOL)
        orders = await client.get_open_orders(SYMBOL)
        assert len(orders) == 0

    async def test_cancel_sell_returns_locked_btc_to_free(self, client_with_btc):
        client_with_btc.set_price(40000.0)
        order = await client_with_btc.place_limit_sell(
            qty_base=0.5, price=55000.0, symbol=SYMBOL
        )
        await client_with_btc.cancel_order(order["orderId"], SYMBOL)
        btc_bal = await client_with_btc.get_balance("BTC")
        assert btc_bal["free"] == pytest.approx(1.0)
        assert btc_bal["locked"] == pytest.approx(0.0)

    async def test_cancel_nonexistent_order_returns_canceled_status(self, client):
        result = await client.cancel_order(99999, SYMBOL)
        assert result["status"] == "CANCELED"
        assert result["orderId"] == 99999

    async def test_cancel_returns_canceled_status(self, client):
        # current price 300 > order price 200 → order stays open
        client.set_price(300.0)
        order = await client.place_limit_buy_by_quote(
            quote_usdt=500.0, price=200.0, symbol=SYMBOL
        )
        result = await client.cancel_order(order["orderId"], SYMBOL)
        assert result["status"] == "CANCELED"


# ---------------------------------------------------------------------------
# get_my_trades
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestGetMyTrades:
    async def test_returns_trades_filtered_by_symbol(self, client):
        # Place buy that fills immediately
        client.set_price(50000.0)
        await client.place_limit_buy_by_quote(
            quote_usdt=500.0, price=50000.0, symbol=SYMBOL
        )
        trades = await client.get_my_trades(SYMBOL)
        assert len(trades) == 1
        assert all(t["symbol"] == SYMBOL for t in trades)

    async def test_respects_limit_parameter(self, client_with_btc):
        client_with_btc.set_price(50000.0)
        # Place 3 sells that fill immediately (price >= order price)
        for _ in range(3):
            btc_free = await client_with_btc.get_free_balance("BTC")
            if btc_free < 0.1:
                break
            await client_with_btc.place_limit_sell(
                qty_base=0.1, price=50000.0, symbol=SYMBOL
            )
        trades = await client_with_btc.get_my_trades(SYMBOL, limit=2)
        assert len(trades) <= 2

    async def test_filters_by_order_id(self, client):
        client.set_price(50000.0)
        order = await client.place_limit_buy_by_quote(
            quote_usdt=500.0, price=50000.0, symbol=SYMBOL
        )
        order_id = order["orderId"]
        trades = await client.get_my_trades(SYMBOL, order_id=order_id)
        assert all(t["orderId"] == order_id for t in trades)

    async def test_returns_empty_for_unknown_order_id(self, client):
        client.set_price(50000.0)
        await client.place_limit_buy_by_quote(
            quote_usdt=500.0, price=50000.0, symbol=SYMBOL
        )
        trades = await client.get_my_trades(SYMBOL, order_id=99999)
        assert trades == []


# ---------------------------------------------------------------------------
# get_order
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestGetOrder:
    async def test_returns_open_order_by_id(self, client):
        # current price 300 > order price 200 → order stays open (not filled)
        client.set_price(300.0)
        order = await client.place_limit_buy_by_quote(
            quote_usdt=500.0, price=200.0, symbol=SYMBOL
        )
        fetched = await client.get_order(order["orderId"], SYMBOL)
        assert fetched["orderId"] == order["orderId"]
        assert fetched["status"] == "NEW"

    async def test_returns_not_found_for_unknown_id(self, client):
        result = await client.get_order(99999, SYMBOL)
        assert result["status"] == "NOT_FOUND"
        assert result["orderId"] == 99999

    async def test_returns_filled_order_after_fill(self, client):
        client.set_price(50000.0)
        order = await client.place_limit_buy_by_quote(
            quote_usdt=500.0, price=50000.0, symbol=SYMBOL
        )
        fetched = await client.get_order(order["orderId"], SYMBOL)
        assert fetched["status"] == "FILLED"


# ---------------------------------------------------------------------------
# adjust_qty and adjust_price
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestAdjustments:
    """step_size=0.00001, tick_size=0.01."""

    async def test_adjust_qty_floors_to_step_size(self, client):
        # 0.123456789 floored to step 0.00001 → 0.12345
        result = await client.adjust_qty(0.123456789, SYMBOL)
        assert result == pytest.approx(0.12345, rel=1e-6)

    async def test_adjust_qty_exact_step_unchanged(self, client):
        result = await client.adjust_qty(0.12345, SYMBOL)
        assert result == pytest.approx(0.12345)

    async def test_adjust_price_floors_to_tick_size(self, client):
        # 12345.678 floored to tick 0.01 → 12345.67
        result = await client.adjust_price(12345.678, SYMBOL)
        assert result == pytest.approx(12345.67)

    async def test_adjust_price_exact_tick_unchanged(self, client):
        result = await client.adjust_price(12345.67, SYMBOL)
        assert result == pytest.approx(12345.67)

    async def test_adjust_qty_rounds_down_not_up(self, client):
        # 0.999999 should floor to 0.99999, not round to 1.00000
        result = await client.adjust_qty(0.999999, SYMBOL)
        assert result < 1.0


# ---------------------------------------------------------------------------
# Full buy → fill → sell → fill cycle
# ---------------------------------------------------------------------------

@pytest.mark.exchange
class TestFullCycle:
    async def test_buy_fill_sell_fill_usdt_balance(self, client):
        """
        Buy 1000 USDT of BTC at 50000, then sell all BTC at 55000.
        Net USDT change = sell proceeds - buy cost.
        """
        initial_usdt = await client.get_free_balance("USDT")

        # Step 1: place buy, price matches → immediate fill
        client.set_price(50000.0)
        buy_order = await client.place_limit_buy_by_quote(
            quote_usdt=1000.0, price=50000.0, symbol=SYMBOL
        )
        btc_qty = float(buy_order["origQty"])
        assert btc_qty > 0

        # Verify USDT was spent
        usdt_after_buy = await client.get_free_balance("USDT")
        assert usdt_after_buy == pytest.approx(initial_usdt - btc_qty * 50000.0, rel=1e-6)

        # Step 2: sell all BTC at 55000
        client.set_price(40000.0)  # below sell price → order stays open
        await client.place_limit_sell(
            qty_base=btc_qty, price=55000.0, symbol=SYMBOL
        )
        # Fill the sell
        client.set_price(55000.0)

        # Verify BTC is gone
        btc_free = await client.get_free_balance("BTC")
        btc_locked = (await client.get_balance("BTC"))["locked"]
        assert btc_free == pytest.approx(0.0, abs=1e-8)
        assert btc_locked == pytest.approx(0.0, abs=1e-8)

        # Verify USDT is back plus profit
        final_usdt = await client.get_free_balance("USDT")
        sell_proceeds = btc_qty * 55000.0
        buy_cost = btc_qty * 50000.0
        expected_usdt = initial_usdt - buy_cost + sell_proceeds
        assert final_usdt == pytest.approx(expected_usdt, rel=1e-6)

    async def test_multiple_buy_orders_tracked_independently(self, client):
        # current price 500 > both order prices (200, 300) → both stay open
        client.set_price(500.0)
        o1 = await client.place_limit_buy_by_quote(
            quote_usdt=200.0, price=200.0, symbol=SYMBOL
        )
        o2 = await client.place_limit_buy_by_quote(
            quote_usdt=300.0, price=300.0, symbol=SYMBOL
        )
        orders = await client.get_open_orders(SYMBOL)
        assert len(orders) == 2
        ids = {o["orderId"] for o in orders}
        assert o1["orderId"] in ids
        assert o2["orderId"] in ids
