"""Unit tests for LotStackingBuy — all mock-based, no DB."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.strategies.base import RepositoryBundle, StrategyContext
from app.strategies.buys.lot_stacking import LotStackingBuy
from app.strategies.constants import PENDING_KEYS

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
        params=LotStackingBuy.default_params.copy(),
        client_order_prefix="TEST_",
        free_balance=10000.0,
        open_lots=None,
    )
    defaults.update(overrides)
    return StrategyContext(**defaults)


def _make_state_store(state_dict: dict | None = None) -> MagicMock:
    """Return a mock StrategyStateStore with sensible defaults."""
    state_dict = state_dict or {}
    store = MagicMock()

    async def _get(key, default=None):
        return state_dict.get(key, default)

    async def _get_float(key, default=0.0):
        val = state_dict.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    async def _get_int(key, default=0):
        val = state_dict.get(key)
        if val is None:
            return default
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return default

    async def _set(key, value):
        state_dict[key] = str(value)

    async def _set_many(items):
        for k, v in items.items():
            state_dict[k] = str(v)

    async def _clear_keys(*keys):
        for k in keys:
            state_dict.pop(k, None)

    async def _preload():
        pass

    store.get = AsyncMock(side_effect=_get)
    store.get_float = AsyncMock(side_effect=_get_float)
    store.get_int = AsyncMock(side_effect=_get_int)
    store.set = AsyncMock(side_effect=_set)
    store.set_many = AsyncMock(side_effect=_set_many)
    store.clear_keys = AsyncMock(side_effect=_clear_keys)
    store.preload = AsyncMock(side_effect=_preload)
    store.with_scope = MagicMock(return_value=MagicMock())
    return store, state_dict


def _make_exchange(
    *,
    order_status="NEW",
    executed_qty="0.001",
    cumulative_quote="50.0",
    order_id=12345,
) -> AsyncMock:
    exchange = AsyncMock()
    exchange.get_free_balance = AsyncMock(return_value=10000.0)
    exchange.get_symbol_filters = AsyncMock(return_value=MagicMock(min_notional=10.0))
    exchange.adjust_price = AsyncMock(side_effect=lambda p, s: p)
    exchange.adjust_qty = AsyncMock(side_effect=lambda q, s: q)
    exchange.place_limit_buy_by_quote = AsyncMock(return_value={"orderId": order_id, "transactTime": 1_000_000})
    exchange.get_order = AsyncMock(
        return_value={
            "orderId": order_id,
            "status": order_status,
            "executedQty": executed_qty,
            "cummulativeQuoteQty": cumulative_quote,
            "updateTime": 1_000_000,
        }
    )
    exchange.cancel_order = AsyncMock(return_value={"orderId": order_id, "status": "CANCELED"})
    return exchange


def _make_repos() -> MagicMock:
    repos = MagicMock(spec=RepositoryBundle)
    repos.lot = MagicMock()
    repos.lot.insert_lot = AsyncMock()
    repos.lot.get_open_lots_by_combo = AsyncMock(return_value=[])
    repos.order = MagicMock()
    repos.order.upsert_order = AsyncMock()
    repos.position = MagicMock()
    repos.price = MagicMock()
    return repos


def _make_account_state() -> MagicMock:
    acct_state = MagicMock()
    acct_state.set_reserve_qty = AsyncMock()
    acct_state.set_reserve_cost_usdt = AsyncMock()
    return acct_state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_buy_above_prebuy_price():
    """When current_price > prebuy threshold no limit order is placed."""
    strategy = LotStackingBuy()
    combo_id = uuid.uuid4()

    # base_price=50000, drop_pct=0.006 -> trigger=49700, prebuy_pct=0.0015 -> prebuy=49774.55
    # Set current_price well above prebuy
    ctx = _make_ctx(price=50000.0)
    state, _ = _make_state_store({"base_price": "50000.0"})
    exchange = _make_exchange()
    repos = _make_repos()
    account_state = _make_account_state()

    await strategy.tick(ctx, state, exchange, account_state, repos, combo_id)

    exchange.place_limit_buy_by_quote.assert_not_called()


@pytest.mark.asyncio
async def test_limit_buy_on_drop():
    """When current_price drops below prebuy threshold a limit buy is placed."""
    strategy = LotStackingBuy()
    combo_id = uuid.uuid4()

    # base_price=50000, trigger=49700, prebuy≈49774 -> price 49000 triggers buy
    ctx = _make_ctx(price=49000.0)
    state, state_dict = _make_state_store({"base_price": "50000.0"})
    exchange = _make_exchange()
    repos = _make_repos()
    account_state = _make_account_state()

    # Ensure cooldown is satisfied (fresh instance, _last_order_ts=0)
    await strategy.tick(ctx, state, exchange, account_state, repos, combo_id)

    exchange.place_limit_buy_by_quote.assert_called_once()
    call_kwargs = exchange.place_limit_buy_by_quote.call_args
    assert call_kwargs.kwargs["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_pending_filled_creates_lot():
    """When a pending order is FILLED, repos.lot.insert_lot is called."""
    strategy = LotStackingBuy()
    combo_id = uuid.uuid4()

    ctx = _make_ctx(price=49000.0)
    state, _ = _make_state_store(
        {
            "pending_order_id": "12345",
            "pending_time_ms": "1000000",
            "pending_bucket_usdt": "0",
            "pending_kind": "LOT",
            "pending_trigger_price": "49700.0",
        }
    )
    exchange = _make_exchange(order_status="FILLED", executed_qty="0.001", cumulative_quote="50.0")
    repos = _make_repos()
    account_state = _make_account_state()

    await strategy.tick(ctx, state, exchange, account_state, repos, combo_id)

    repos.lot.insert_lot.assert_called_once()
    call_kwargs = repos.lot.insert_lot.call_args.kwargs
    assert call_kwargs["account_id"] == ctx.account_id
    assert call_kwargs["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_pending_rebound_cancels():
    """When price rebounds above cancel_rebound_pct, the pending order is cancelled."""
    strategy = LotStackingBuy()
    combo_id = uuid.uuid4()

    # trigger=49700, cancel_rebound_pct=0.004 -> rebound=49700*(1.004)=49898.8
    # Set price above rebound threshold
    ctx = _make_ctx(price=49900.0)
    state, _ = _make_state_store(
        {
            "pending_order_id": "12345",
            "pending_time_ms": "1000000",
            "pending_bucket_usdt": "0",
            "pending_kind": "LOT",
            "pending_trigger_price": "49700.0",
        }
    )
    exchange = _make_exchange(order_status="NEW")
    repos = _make_repos()
    account_state = _make_account_state()

    await strategy.tick(ctx, state, exchange, account_state, repos, combo_id)

    exchange.cancel_order.assert_called_once_with(12345, "BTCUSDT")


@pytest.mark.asyncio
async def test_cooldown_prevents_buy():
    """When _last_order_ts is recent, _cooldown_ok returns False and no order is placed."""
    strategy = LotStackingBuy()
    combo_id = uuid.uuid4()

    # Set _last_order_ts to "now" so cooldown is not satisfied
    strategy._last_order_ts = strategy._now()

    ctx = _make_ctx(price=49000.0)
    state, _ = _make_state_store({"base_price": "50000.0"})
    exchange = _make_exchange()
    repos = _make_repos()
    account_state = _make_account_state()

    await strategy.tick(ctx, state, exchange, account_state, repos, combo_id)

    exchange.place_limit_buy_by_quote.assert_not_called()


@pytest.mark.asyncio
async def test_recenter_ema():
    """When EMA rises above recenter threshold, base_price is updated to EMA."""
    strategy = LotStackingBuy()
    combo_id = uuid.uuid4()

    # base_price=50000, recenter_pct=0.02 -> threshold=51000
    # Use a high current_price so EMA initialises above threshold
    ctx = _make_ctx(price=52000.0, open_lots=[])  # open_lots=[] so recenter can run
    state, state_dict = _make_state_store(
        {
            "base_price": "50000.0",
            "recenter_ema": "0",  # will be initialised to current_price on first call
        }
    )
    exchange = _make_exchange()
    repos = _make_repos()

    # pre_tick runs recenter logic
    await strategy.pre_tick(ctx, state, exchange, repos, combo_id)

    # EMA initialised to 52000 (prev=0 branch), which is > 50000*1.02=51000
    assert float(state_dict.get("base_price", 0)) == pytest.approx(52000.0)


@pytest.mark.asyncio
async def test_set_many_called_on_buy():
    """After placing a LOT buy order, set_many is called with all pending keys."""
    strategy = LotStackingBuy()
    combo_id = uuid.uuid4()

    ctx = _make_ctx(price=49000.0)
    state, _ = _make_state_store({"base_price": "50000.0"})
    exchange = _make_exchange()
    repos = _make_repos()
    account_state = _make_account_state()

    await strategy.tick(ctx, state, exchange, account_state, repos, combo_id)

    state.set_many.assert_called_once()
    passed_dict = state.set_many.call_args.args[0]
    for key in PENDING_KEYS:
        assert key in passed_dict, f"Expected pending key '{key}' in set_many call"


@pytest.mark.asyncio
async def test_handle_filled_buy_init_kind():
    """_handle_filled_buy with kind=INIT sets reserve qty/cost and core_btc_initial."""
    strategy = LotStackingBuy()
    combo_id = uuid.uuid4()

    ctx = _make_ctx(price=49000.0)
    state, state_dict = _make_state_store()
    account_state = _make_account_state()
    repos = _make_repos()

    order_data = {
        "orderId": 99999,
        "status": "FILLED",
        "executedQty": "0.002",
        "cummulativeQuoteQty": "98.0",
        "updateTime": 1_700_000_000_000,
    }

    await strategy._handle_filled_buy(
        ctx, state, order_data, account_state, repos, combo_id, kind="INIT"
    )

    account_state.set_reserve_qty.assert_called_once()
    account_state.set_reserve_cost_usdt.assert_called_once()
    # insert_lot must NOT be called for INIT kind
    repos.lot.insert_lot.assert_not_called()
    # core_btc_initial must be stored in state
    assert "core_btc_initial" in state_dict


@pytest.mark.asyncio
async def test_scaled_plan_round_increment():
    """After a LOT fill with sizing_mode=scaled_plan, sizing_round increments by 1."""
    strategy = LotStackingBuy()
    combo_id = uuid.uuid4()

    params = LotStackingBuy.default_params.copy()
    params["sizing_mode"] = "scaled_plan"
    ctx = _make_ctx(price=49000.0, params=params)
    state, state_dict = _make_state_store({"sizing_round": "2"})
    account_state = _make_account_state()
    repos = _make_repos()

    order_data = {
        "orderId": 77777,
        "status": "FILLED",
        "executedQty": "0.001",
        "cummulativeQuoteQty": "49.0",
        "updateTime": 1_700_000_000_000,
    }

    await strategy._handle_filled_buy(
        ctx, state, order_data, account_state, repos, combo_id, kind="LOT"
    )

    assert int(float(state_dict["sizing_round"])) == 3


@pytest.mark.asyncio
async def test_balance_error_raises():
    """place_limit_buy_by_quote raising an 'insufficient' error propagates out of tick."""
    strategy = LotStackingBuy()
    combo_id = uuid.uuid4()

    ctx = _make_ctx(price=49000.0)
    state, _ = _make_state_store({"base_price": "50000.0"})
    exchange = _make_exchange()
    exchange.place_limit_buy_by_quote = AsyncMock(
        side_effect=Exception("insufficient balance to execute order")
    )
    repos = _make_repos()
    account_state = _make_account_state()

    with pytest.raises(Exception, match="insufficient"):
        await strategy.tick(ctx, state, exchange, account_state, repos, combo_id)
