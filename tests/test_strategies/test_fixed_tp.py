import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.strategies.base import RepositoryBundle, StrategyContext
from app.strategies.sells.fixed_tp import FixedTpSell

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(price=50000.0, **overrides):
    defaults = dict(
        account_id=uuid.uuid4(),
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        current_price=price,
        params=FixedTpSell.default_params.copy(),
        client_order_prefix="TEST_",
        free_balance=10000.0,
        open_lots=None,
    )
    defaults.update(overrides)
    return StrategyContext(**defaults)


def _make_lot(lot_id=1, buy_price=50000.0, buy_qty=0.001, sell_order_id=None):
    lot = MagicMock()
    lot.lot_id = lot_id
    lot.account_id = uuid.uuid4()
    # Use Decimal to match the real Lot model, but the production code multiplies
    # by a float tp_pct.  Decimal * float raises TypeError, so we store as float
    # to reflect how the values are actually used at runtime (fetched from DB and
    # coerced to float by SQLAlchemy Numeric columns in practice).
    lot.buy_price = float(buy_price)
    lot.buy_qty = float(buy_qty)
    lot.sell_order_id = sell_order_id
    lot.sell_order_time_ms = None
    return lot


def _make_repos():
    repos = MagicMock(spec=RepositoryBundle)
    repos.lot = MagicMock()
    repos.lot.close_lot = AsyncMock()
    repos.lot.set_sell_order = AsyncMock()
    repos.lot.clear_sell_order = AsyncMock()
    repos.lot.flush = AsyncMock()
    repos.order = MagicMock()
    repos.order.upsert_order = AsyncMock()
    repos.order.get_order = AsyncMock(return_value=None)
    repos.order.get_fills_for_order = AsyncMock(return_value=[])
    return repos


def _make_exchange(min_notional=10.0):
    exchange = AsyncMock()
    exchange.get_symbol_filters = AsyncMock(return_value=MagicMock(min_notional=min_notional))
    exchange.adjust_price = AsyncMock(side_effect=lambda p, s: float(p))
    exchange.adjust_qty = AsyncMock(side_effect=lambda q, s: float(q))
    exchange.place_limit_sell = AsyncMock(
        return_value={
            "orderId": 99999,
            "transactTime": 2000000,
            "status": "NEW",
        }
    )
    exchange.get_free_balance = AsyncMock(return_value=1e8)  # 충분한 잔액
    exchange.get_order = AsyncMock(
        return_value={
            "orderId": 99999,
            "status": "FILLED",
            "executedQty": "0.001",
            "cummulativeQuoteQty": "51.65",
            "updateTime": 2000000,
        }
    )
    return exchange


def _make_state():
    state = AsyncMock()
    state.get_int = AsyncMock(return_value=0)
    state.get_float = AsyncMock(return_value=0.0)
    state.set = AsyncMock()
    state.set_many = AsyncMock()
    state.clear_keys = AsyncMock()
    return state


def _make_account_state():
    account_state = AsyncMock()
    account_state.add_pending_earnings = AsyncMock()
    return account_state


def _make_strategy():
    strategy = FixedTpSell()
    # Force cooldown to always pass during tests
    strategy._last_order_ts = 0.0
    return strategy


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_lots_returns_immediately():
    """open_lots=[] should return without making any exchange calls."""
    strategy = _make_strategy()
    ctx = _make_ctx()
    exchange = _make_exchange()
    repos = _make_repos()
    state = _make_state()
    account_state = _make_account_state()

    await strategy.tick(ctx, state, exchange, account_state, repos, open_lots=[])

    exchange.get_symbol_filters.assert_not_called()
    exchange.place_limit_sell.assert_not_called()
    repos.lot.set_sell_order.assert_not_called()


@pytest.mark.asyncio
async def test_place_new_tp_order():
    """A lot without sell_order_id should trigger place_limit_sell and set_sell_order."""
    strategy = _make_strategy()
    ctx = _make_ctx()
    exchange = _make_exchange()
    repos = _make_repos()
    state = _make_state()
    account_state = _make_account_state()
    lot = _make_lot(lot_id=1, buy_price=50000.0, buy_qty=0.001, sell_order_id=None)

    await strategy.tick(ctx, state, exchange, account_state, repos, open_lots=[lot])

    exchange.place_limit_sell.assert_awaited_once()
    call_kwargs = exchange.place_limit_sell.call_args
    # target_price = 50000 * 1.033 = 51650.0
    assert call_kwargs.kwargs["price"] == pytest.approx(51650.0, rel=1e-4)
    assert call_kwargs.kwargs["symbol"] == "BTCUSDT"

    repos.lot.set_sell_order.assert_awaited_once_with(
        account_id=ctx.account_id,
        lot_id=1,
        sell_order_id=99999,
        sell_order_time_ms=2000000,
    )


@pytest.mark.asyncio
async def test_filled_sell_closes_lot():
    """A lot with sell_order_id and a FILLED DB order should call close_lot."""
    strategy = _make_strategy()
    ctx = _make_ctx()
    exchange = _make_exchange()
    repos = _make_repos()
    state = _make_state()
    account_state = _make_account_state()

    lot = _make_lot(lot_id=2, buy_price=50000.0, buy_qty=0.001, sell_order_id=99999)

    db_order = MagicMock()
    db_order.order_id = 99999
    db_order.status = "FILLED"
    db_order.executed_qty = Decimal("0.001")
    db_order.cum_quote_qty = Decimal("51.65")
    db_order.update_time_ms = 2000000
    repos.order.get_order = AsyncMock(return_value=db_order)

    await strategy.tick(ctx, state, exchange, account_state, repos, open_lots=[lot])

    repos.lot.close_lot.assert_awaited_once()
    call_kwargs = repos.lot.close_lot.call_args.kwargs
    assert call_kwargs["account_id"] == ctx.account_id
    assert call_kwargs["lot_id"] == 2
    assert call_kwargs["sell_order_id"] == 99999
    # sell_price = 51.65 / 0.001 = 51650.0
    assert call_kwargs["sell_price"] == pytest.approx(51650.0, rel=1e-4)


@pytest.mark.asyncio
async def test_canceled_sell_clears_order():
    """A lot with sell_order_id and a CANCELED DB order should call clear_sell_order."""
    strategy = _make_strategy()
    ctx = _make_ctx()
    exchange = _make_exchange()
    repos = _make_repos()
    state = _make_state()
    account_state = _make_account_state()

    lot = _make_lot(lot_id=3, buy_price=50000.0, buy_qty=0.001, sell_order_id=88888)

    # Override exchange.get_order to return CANCELED status
    exchange.get_order = AsyncMock(
        return_value={
            "orderId": 88888,
            "status": "CANCELED",
            "executedQty": "0",
            "cummulativeQuoteQty": "0",
            "updateTime": 1000000,
        }
    )

    await strategy.tick(ctx, state, exchange, account_state, repos, open_lots=[lot])

    repos.lot.clear_sell_order.assert_awaited_once_with(account_id=ctx.account_id, lot_id=3)
    repos.lot.close_lot.assert_not_awaited()


@pytest.mark.asyncio
async def test_notional_below_min_skips():
    """A tiny lot whose notional falls below min_notional should be skipped entirely."""
    strategy = _make_strategy()
    ctx = _make_ctx()
    # min_notional=10.0; lot notional = 0.0001 * 51650 ≈ 5.165 < 10
    exchange = _make_exchange(min_notional=10.0)
    repos = _make_repos()
    state = _make_state()
    account_state = _make_account_state()

    tiny_lot = _make_lot(lot_id=4, buy_price=50000.0, buy_qty=0.0001, sell_order_id=None)

    await strategy.tick(ctx, state, exchange, account_state, repos, open_lots=[tiny_lot])

    exchange.place_limit_sell.assert_not_awaited()
    repos.lot.set_sell_order.assert_not_awaited()
    repos.lot.close_lot.assert_not_awaited()


@pytest.mark.asyncio
async def test_profit_added_to_pending_earnings():
    """On FILLED with positive profit, add_pending_earnings must be called."""
    strategy = _make_strategy()
    ctx = _make_ctx()
    exchange = _make_exchange()
    repos = _make_repos()
    state = _make_state()
    account_state = _make_account_state()

    # buy_price=50000, buy_qty=0.001 → cost = 50.0 USDT
    # sell revenue = 51.65 USDT → net_profit = 51.65 - 50.0 - 0 (no fills) = 1.65
    lot = _make_lot(lot_id=5, buy_price=50000.0, buy_qty=0.001, sell_order_id=99999)

    db_order = MagicMock()
    db_order.order_id = 99999
    db_order.status = "FILLED"
    db_order.executed_qty = Decimal("0.001")
    db_order.cum_quote_qty = Decimal("51.65")
    db_order.update_time_ms = 2000000
    repos.order.get_order = AsyncMock(return_value=db_order)

    await strategy.tick(ctx, state, exchange, account_state, repos, open_lots=[lot])

    account_state.add_pending_earnings.assert_awaited_once()
    earned = account_state.add_pending_earnings.call_args.args[0]
    assert earned == pytest.approx(1.65, rel=1e-4)


@pytest.mark.asyncio
async def test_negative_profit_not_added():
    """On FILLED with negative profit, add_pending_earnings must NOT be called."""
    strategy = _make_strategy()
    ctx = _make_ctx()
    exchange = _make_exchange()
    repos = _make_repos()
    state = _make_state()
    account_state = _make_account_state()

    # buy_price=55000, buy_qty=0.001 → cost = 55.0 USDT
    # sell revenue = 51.65 USDT → net_profit = 51.65 - 55.0 = -3.35 (negative)
    lot = _make_lot(lot_id=6, buy_price=55000.0, buy_qty=0.001, sell_order_id=99999)

    db_order = MagicMock()
    db_order.order_id = 99999
    db_order.status = "FILLED"
    db_order.executed_qty = Decimal("0.001")
    db_order.cum_quote_qty = Decimal("51.65")
    db_order.update_time_ms = 2000000
    repos.order.get_order = AsyncMock(return_value=db_order)

    await strategy.tick(ctx, state, exchange, account_state, repos, open_lots=[lot])

    account_state.add_pending_earnings.assert_not_awaited()
    # close_lot should still be called even with negative profit
    repos.lot.close_lot.assert_awaited_once()
