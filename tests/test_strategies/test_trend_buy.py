"""Unit tests for TrendBuy._process_pending_trend_buy — all mock-based, no DB."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.strategies.base import RepositoryBundle, StrategyContext
from app.strategies.buys.trend import TrendBuy
from app.strategies.constants import PENDING_KEYS

pytestmark = pytest.mark.asyncio

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
        params=TrendBuy.default_params.copy(),
        client_order_prefix="TEST_",
        free_balance=10000.0,
        open_lots=None,
    )
    defaults.update(overrides)
    return StrategyContext(**defaults)


def _make_state_store(state_dict: dict | None = None) -> MagicMock:
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


@pytest.mark.unit
class TestTrendBuyPending:
    """Tests for TrendBuy._process_pending_trend_buy"""

    async def test_no_pending_order_returns_false(self):
        """Empty state returns False (enters _maybe_buy_on_trend)."""
        strategy = TrendBuy()
        combo_id = uuid.uuid4()
        ctx = _make_ctx()
        state, _ = _make_state_store()  # No pending_order_id
        exchange = _make_exchange()
        repos = _make_repos()
        acct = _make_account_state()
        result = await strategy._process_pending_trend_buy(ctx, state, exchange, acct, repos, combo_id)
        assert result is False

    async def test_filled_order_creates_lot(self):
        """FILLED order creates lot via repos.lot.insert_lot and clears pending keys."""
        strategy = TrendBuy()
        combo_id = uuid.uuid4()
        ctx = _make_ctx()
        state, state_dict = _make_state_store(
            {
                "pending_order_id": "12345",
                "pending_time_ms": "1000000",
                "pending_bucket_usdt": "0",
                "pending_trigger_price": "49700.0",
            }
        )
        exchange = _make_exchange(order_status="FILLED", executed_qty="0.001", cumulative_quote="50.0")
        repos = _make_repos()
        acct = _make_account_state()

        result = await strategy._process_pending_trend_buy(ctx, state, exchange, acct, repos, combo_id)

        assert result is True
        repos.lot.insert_lot.assert_called_once()
        kwargs = repos.lot.insert_lot.call_args.kwargs
        assert kwargs["account_id"] == ctx.account_id
        assert kwargs["symbol"] == "BTCUSDT"
        assert kwargs["strategy_name"] == "trend_buy"
        assert kwargs["combo_id"] == combo_id
        assert kwargs["buy_price"] == pytest.approx(50000.0)  # 50.0 / 0.001
        assert kwargs["buy_qty"] == pytest.approx(0.001)
        # Pending keys should be cleared
        for key in PENDING_KEYS:
            assert key not in state_dict

    async def test_canceled_order_clears_state(self):
        """CANCELED order clears pending keys, no lot created."""
        strategy = TrendBuy()
        combo_id = uuid.uuid4()
        ctx = _make_ctx()
        state, state_dict = _make_state_store(
            {
                "pending_order_id": "12345",
                "pending_time_ms": "1000000",
                "pending_bucket_usdt": "0",
                "pending_trigger_price": "49700.0",
            }
        )
        exchange = _make_exchange(order_status="CANCELED")
        repos = _make_repos()
        acct = _make_account_state()

        result = await strategy._process_pending_trend_buy(ctx, state, exchange, acct, repos, combo_id)

        assert result is True
        repos.lot.insert_lot.assert_not_called()
        for key in PENDING_KEYS:
            assert key not in state_dict

    async def test_timeout_cancels_order(self):
        """Order older than 3h triggers cancel_order and clears pending keys."""
        strategy = TrendBuy()
        combo_id = uuid.uuid4()
        ctx = _make_ctx()
        old_time_ms = int((strategy._now() - 4 * 3600) * 1000)  # 4 hours ago
        state, state_dict = _make_state_store(
            {
                "pending_order_id": "12345",
                "pending_time_ms": str(old_time_ms),
                "pending_bucket_usdt": "0",
                "pending_trigger_price": "49700.0",
            }
        )
        exchange = _make_exchange(order_status="NEW")  # Still NEW after 4 hours
        repos = _make_repos()
        acct = _make_account_state()

        result = await strategy._process_pending_trend_buy(ctx, state, exchange, acct, repos, combo_id)

        assert result is True
        exchange.cancel_order.assert_called_once_with(12345, "BTCUSDT")
        for key in PENDING_KEYS:
            assert key not in state_dict

    async def test_timeout_cancel_failure_still_clears_state(self):
        """Even if cancel_order raises, pending keys are still cleared."""
        strategy = TrendBuy()
        combo_id = uuid.uuid4()
        ctx = _make_ctx()
        old_time_ms = int((strategy._now() - 4 * 3600) * 1000)
        state, state_dict = _make_state_store(
            {
                "pending_order_id": "12345",
                "pending_time_ms": str(old_time_ms),
                "pending_bucket_usdt": "0",
                "pending_trigger_price": "49700.0",
            }
        )
        exchange = _make_exchange(order_status="NEW")
        exchange.cancel_order = AsyncMock(side_effect=Exception("Exchange error"))
        repos = _make_repos()
        acct = _make_account_state()

        result = await strategy._process_pending_trend_buy(ctx, state, exchange, acct, repos, combo_id)

        assert result is True
        for key in PENDING_KEYS:
            assert key not in state_dict

    async def test_filled_order_deducts_commission(self):
        """When fills include base commission, buy_qty is net of fees."""
        strategy = TrendBuy()
        combo_id = uuid.uuid4()
        ctx = _make_ctx()
        state, _ = _make_state_store(
            {
                "pending_order_id": "12345",
                "pending_time_ms": "1000000",
                "pending_bucket_usdt": "0",
                "pending_trigger_price": "49700.0",
            }
        )
        exchange = _make_exchange(order_status="FILLED", executed_qty="0.001", cumulative_quote="50.0")
        exchange.get_order = AsyncMock(
            return_value={
                "orderId": 12345,
                "status": "FILLED",
                "executedQty": "0.001",
                "cummulativeQuoteQty": "50.0",
                "updateTime": 1_000_000,
                "fills": [{"commissionAsset": "BTC", "commission": "0.0000001", "qty": "0.001", "price": "50000.0"}],
            }
        )
        repos = _make_repos()
        acct = _make_account_state()

        await strategy._process_pending_trend_buy(ctx, state, exchange, acct, repos, combo_id)

        kwargs = repos.lot.insert_lot.call_args.kwargs
        # 0.001 - 0.0000001 = 0.0009999
        assert kwargs["buy_qty"] == pytest.approx(0.0009999)

    async def test_fetch_order_failure_returns_true(self):
        """When get_order raises, returns True (stays pending, doesn't crash)."""
        strategy = TrendBuy()
        combo_id = uuid.uuid4()
        ctx = _make_ctx()
        state, state_dict = _make_state_store(
            {
                "pending_order_id": "12345",
                "pending_time_ms": "1000000",
                "pending_bucket_usdt": "0",
                "pending_trigger_price": "49700.0",
            }
        )
        exchange = _make_exchange()
        exchange.get_order = AsyncMock(side_effect=Exception("Connection failed"))
        repos = _make_repos()
        acct = _make_account_state()

        result = await strategy._process_pending_trend_buy(ctx, state, exchange, acct, repos, combo_id)

        assert result is True
        # Pending keys should NOT be cleared (order might still be live)
        assert "pending_order_id" in state_dict
