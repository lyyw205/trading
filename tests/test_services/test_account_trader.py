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
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.THROTTLED, is_balance_sufficient=True, throttle_cycle=cycle
        )
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


@pytest.mark.asyncio
async def test_stop_async_clears_client(trader):
    """stop_async() must set _running=False and call client.close()."""
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    trader._client = mock_client
    assert trader._running is True

    await trader.stop_async()

    assert trader._running is False
    mock_client.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# _do_step tests
# ---------------------------------------------------------------------------


def _make_account_mock(
    *,
    is_active: bool = True,
    buy_pause_state: str = "ACTIVE",
    consecutive_low_balance: int = 0,
    quote_asset: str = "USDT",
    symbol: str = "BTCUSDT",
    loop_interval_sec: int = 30,
    circuit_breaker_failures: int = 0,
    auto_recovery_attempts: int = 0,
) -> MagicMock:
    """Build a mock TradingAccount with the attrs _do_step() reads."""
    account = MagicMock()
    account.id = uuid.uuid4()
    account.owner_id = uuid.uuid4()
    account.is_active = is_active
    account.buy_pause_state = buy_pause_state
    account.consecutive_low_balance = consecutive_low_balance
    account.quote_asset = quote_asset
    account.symbol = symbol
    account.loop_interval_sec = loop_interval_sec
    account.circuit_breaker_failures = circuit_breaker_failures
    account.auto_recovery_attempts = auto_recovery_attempts
    return account


def _make_combo_mock(account_id, symbol: str = "BTCUSDT") -> MagicMock:
    combo = MagicMock()
    combo.id = uuid.uuid4()
    combo.account_id = account_id
    combo.is_enabled = True
    combo.symbols = [symbol]
    combo.buy_logic_name = "lot_stacking"
    combo.sell_logic_name = "fixed_tp"
    combo.buy_params = {}
    combo.sell_params = {}
    combo.reference_combo_id = None
    return combo


def _make_async_cm(session):
    """Return a callable that, when called, returns an async context manager yielding session."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=cm)
    return factory


def _build_step_mocks(trader, account, combos, *, open_lots_before=0, open_lots_after=0):
    """Wire all mocks needed for _do_step(). Returns (extras_dict, list_of_patch_objects).

    Callers start all patches with ExitStack.enter_context() before calling _do_step().
    """
    from unittest.mock import patch

    # --- Session ---
    session = AsyncMock()
    session.commit = AsyncMock()
    nested_ctx = AsyncMock()
    nested_ctx.__aenter__ = AsyncMock(return_value=nested_ctx)
    nested_ctx.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested_ctx)

    # session.execute call sequence:
    #   call 0 → combo query result
    #   call 1 → open_lots count
    #   further calls → MAX(trade_id) result (empty)
    combo_scalars = MagicMock()
    combo_scalars.all.return_value = combos
    combo_result = MagicMock()
    combo_result.scalars.return_value = combo_scalars

    count_result = MagicMock()
    count_result.scalar_one.return_value = open_lots_after

    # Fallback for MAX(trade_id) query and orphan-reconcile lot/order queries
    generic_result = MagicMock()
    generic_result.all.return_value = []
    generic_result.scalar_one.return_value = 0

    def _route_execute(stmt, *args, **kwargs):
        stmt_str = str(stmt)
        if "trading_combos" in stmt_str:
            return combo_result
        if "count(" in stmt_str.lower() and "lots" in stmt_str.lower():
            return count_result
        return generic_result

    session.execute = AsyncMock(side_effect=lambda stmt, *a, **kw: _route_execute(stmt, *a, **kw))

    # --- Repositories ---
    account_repo = MagicMock()
    account_repo.get_by_id = AsyncMock(return_value=account)
    account_repo.update_last_success = AsyncMock()
    account_repo.reset_auto_recovery_on_success = AsyncMock()

    order_repo = MagicMock()
    order_repo.get_recent_open_orders = AsyncMock(return_value=[])
    order_repo.upsert_orders_batch = AsyncMock()
    order_repo.insert_fills_batch = AsyncMock()

    position_repo = MagicMock()
    position_repo.recompute_from_fills = AsyncMock()

    lot_repo = MagicMock()
    open_lots = [MagicMock(combo_id=c.id, symbol=c.symbols[0]) for c in combos for _ in range(open_lots_before)]
    lot_repo.get_all_open_lots_for_account = AsyncMock(return_value=open_lots)

    # Orphan-reconcile query (select Lot.lot_id): return no orphans
    orphan_result = MagicMock()
    orphan_result.all.return_value = []

    # --- Strategy state stores ---
    account_state = MagicMock()
    account_state.preload = AsyncMock()

    combo_state = MagicMock()
    combo_state.preload = AsyncMock()

    # --- Strategy logic instances ---
    buy_logic = MagicMock()
    buy_logic.pre_tick = AsyncMock()
    buy_logic.tick = AsyncMock()
    buy_logic.validate_params = MagicMock(return_value={})

    sell_logic = MagicMock()
    sell_logic.tick = AsyncMock()
    sell_logic.validate_params = MagicMock(return_value={})

    # --- Exchange client ---
    trader._client = AsyncMock()
    trader._client.get_free_balance = AsyncMock(return_value=100.0)
    trader._client.get_open_orders = AsyncMock(return_value=[])
    trader._client.get_my_trades = AsyncMock(return_value=[])
    trader._client.get_my_trades_from_id = AsyncMock(return_value=[])
    trader._rate_limiter.acquire = AsyncMock()

    # Registry class-level mocks
    buy_registry_mock = MagicMock()
    buy_registry_mock.create_instance = MagicMock(return_value=buy_logic)
    sell_registry_mock = MagicMock()
    sell_registry_mock.create_instance = MagicMock(return_value=sell_logic)

    patches = [
        patch("app.services.account_trader.TradingSessionLocal", new=_make_async_cm(session)),
        patch("app.services.account_trader.AccountRepository", return_value=account_repo),
        patch("app.services.account_trader.OrderRepository", return_value=order_repo),
        patch("app.services.account_trader.PositionRepository", return_value=position_repo),
        patch("app.services.account_trader.LotRepository", return_value=lot_repo),
        patch("app.services.account_trader.AccountStateManager", return_value=account_state),
        patch("app.services.account_trader.StrategyStateStore", return_value=combo_state),
        patch("app.services.account_trader.BuyLogicRegistry", buy_registry_mock),
        patch("app.services.account_trader.SellLogicRegistry", sell_registry_mock),
    ]

    extras = {
        "session": session,
        "account_repo": account_repo,
        "buy_logic": buy_logic,
        "sell_logic": sell_logic,
        "lot_repo": lot_repo,
    }
    return extras, patches


@pytest.mark.unit
@pytest.mark.asyncio
async def test_do_step_happy_path_single_combo(trader):
    """1 combo, 1 symbol: pre_tick → sell.tick → buy.tick order preserved; session.commit called."""
    import contextlib

    account = _make_account_mock()
    combo = _make_combo_mock(trader.account_id)
    extras, patches = _build_step_mocks(trader, account, [combo])

    call_order = []

    async def _pre_tick(*a, **kw):
        call_order.append("pre_tick")

    async def _sell_tick(*a, **kw):
        call_order.append("sell_tick")

    async def _buy_tick(*a, **kw):
        call_order.append("buy_tick")

    extras["buy_logic"].pre_tick.side_effect = _pre_tick
    extras["sell_logic"].tick.side_effect = _sell_tick
    extras["buy_logic"].tick.side_effect = _buy_tick

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = await trader._do_step()

    assert result == account.loop_interval_sec
    assert call_order == ["pre_tick", "sell_tick", "buy_tick"]
    extras["session"].commit.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_do_step_inactive_account_returns_early(trader):
    """account.is_active=False → _do_step returns 60, no strategy calls."""
    import contextlib

    account = _make_account_mock(is_active=False)
    extras, patches = _build_step_mocks(trader, account, [])

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = await trader._do_step()

    assert result == 60
    extras["buy_logic"].pre_tick.assert_not_called()
    extras["sell_logic"].tick.assert_not_called()
    extras["buy_logic"].tick.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_do_step_no_combos_returns_early(trader):
    """0 enabled combos → _do_step returns 60, no strategy calls."""
    import contextlib

    account = _make_account_mock()
    extras, patches = _build_step_mocks(trader, account, [])

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = await trader._do_step()

    assert result == 60
    extras["buy_logic"].pre_tick.assert_not_called()
    extras["sell_logic"].tick.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_do_step_paused_skips_buy(trader):
    """buy_pause_state=PAUSED → sell.tick called, buy.tick NOT called."""
    import contextlib

    account = _make_account_mock(buy_pause_state="PAUSED")
    combo = _make_combo_mock(trader.account_id)
    extras, patches = _build_step_mocks(trader, account, [combo])

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await trader._do_step()

    extras["sell_logic"].tick.assert_awaited_once()
    extras["buy_logic"].tick.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_do_step_sell_detected_recheck_balance(trader):
    """open_lots decreased + PAUSED → get_free_balance called a second time."""
    import contextlib

    account = _make_account_mock(buy_pause_state="PAUSED")
    combo = _make_combo_mock(trader.account_id)
    # open_lots_before=1, open_lots_after=0 → did_sell_occur=True
    extras, patches = _build_step_mocks(trader, account, [combo], open_lots_before=1, open_lots_after=0)

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await trader._do_step()

    # get_free_balance: once for initial balance check, once for recheck after sell
    assert trader._client.get_free_balance.await_count >= 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_do_step_resets_failure_counter_on_success(trader):
    """After a successful _do_step, consecutive_failures==0 and failure_history is empty."""
    import contextlib

    trader._consecutive_failures = 3
    trader._failure_history = ["err1", "err2", "err3"]

    account = _make_account_mock()
    combo = _make_combo_mock(trader.account_id)
    extras, patches = _build_step_mocks(trader, account, [combo])

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await trader._do_step()

    assert trader._consecutive_failures == 0
    assert trader._failure_history == []


# ---------------------------------------------------------------------------
# Balance error → buy pause state transition (fix for cd4a8b0 regression)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_balance_error_transitions_to_throttled(trader):
    """BALANCE error in buy_logic.tick must NOT crash _do_step.

    The error should be caught, is_balance_sufficient overridden to False,
    and _post_cycle_sell_check should run → state transitions to THROTTLED.
    Session must be committed so the state persists in DB.
    """
    import contextlib

    account = _make_account_mock(buy_pause_state="ACTIVE", consecutive_low_balance=0)
    combo = _make_combo_mock(trader.account_id)
    extras, patches = _build_step_mocks(trader, account, [combo])

    # Simulate Binance insufficient balance error from buy_logic.tick
    balance_exc = RuntimeError("APIError(code=-2010): Account has insufficient balance for requested action.")

    from unittest.mock import patch as mock_patch

    from app.utils.error_classification import ErrorType

    extras["buy_logic"].tick.side_effect = balance_exc

    # classify_error should return BALANCE for this exception
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(
            mock_patch(
                "app.services.account_trader.classify_error",
                return_value=ErrorType.BALANCE,
            )
        )
        result = await trader._do_step()

    # _do_step must complete normally (not crash)
    assert result == account.loop_interval_sec
    # sell_logic must have run (before buy error)
    extras["sell_logic"].tick.assert_awaited_once()
    # session must be committed (state persists to DB)
    extras["session"].commit.assert_awaited_once()
    # balance error flag must be set
    assert trader._balance_error_in_cycle is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_balance_error_sell_still_runs_for_all_symbols(trader):
    """With multi-symbol combo, BALANCE error on first symbol must NOT
    prevent sell_logic from running on the second symbol.
    """
    import contextlib

    account = _make_account_mock(buy_pause_state="ACTIVE")
    # Multi-symbol combo
    combo = MagicMock()
    combo.id = uuid.uuid4()
    combo.account_id = trader.account_id
    combo.is_enabled = True
    combo.symbols = ["ETHUSDT", "BTCUSDT"]
    combo.buy_logic_name = "lot_stacking"
    combo.sell_logic_name = "fixed_tp"
    combo.buy_params = {}
    combo.sell_params = {}
    combo.reference_combo_id = None

    extras, patches = _build_step_mocks(trader, account, [combo])

    sell_symbols = []

    async def _sell_tick(ctx, *a, **kw):
        sell_symbols.append(ctx.symbol)

    extras["sell_logic"].tick.side_effect = _sell_tick

    balance_exc = RuntimeError("APIError(code=-2010): insufficient balance")

    from unittest.mock import patch as mock_patch

    from app.utils.error_classification import ErrorType

    extras["buy_logic"].tick.side_effect = balance_exc

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(
            mock_patch(
                "app.services.account_trader.classify_error",
                return_value=ErrorType.BALANCE,
            )
        )
        await trader._do_step()

    # Both symbols must have sell_logic executed
    assert len(sell_symbols) == 2
    assert set(sell_symbols) == {"ETHUSDT", "BTCUSDT"}
    # Session committed
    extras["session"].commit.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_balance_error_still_propagates(trader):
    """Non-BALANCE errors in buy_logic.tick must still propagate (not swallowed)."""
    import contextlib

    account = _make_account_mock()
    combo = _make_combo_mock(trader.account_id)
    extras, patches = _build_step_mocks(trader, account, [combo])

    extras["buy_logic"].tick.side_effect = RuntimeError("some transient network error")

    from unittest.mock import patch as mock_patch

    from app.utils.error_classification import ErrorType

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(
            mock_patch(
                "app.services.account_trader.classify_error",
                return_value=ErrorType.TRANSIENT,
            )
        )
        with pytest.raises(RuntimeError, match="some transient network error"):
            await trader._do_step()


# ---------------------------------------------------------------------------
# Circuit breaker tests (run_forever level)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_do_step_circuit_breaker_permanent_error(trader):
    """A PERMANENT error in run_forever trips the CB and disables the account."""
    from unittest.mock import patch

    from app.services.account_trader import CB_FAILURE_THRESHOLD
    from app.utils.error_classification import ErrorType

    permanent_exc = RuntimeError("Invalid API-key -2015")
    disabled_calls = []

    async def _fake_init_client(self_inner):
        raise permanent_exc

    async def _fake_disable(self_inner):
        disabled_calls.append(self_inner._consecutive_failures)

    with (
        patch.object(type(trader), "_init_client", _fake_init_client),
        patch.object(type(trader), "_disable_with_circuit_breaker", _fake_disable),
        patch("app.services.account_trader.classify_error", return_value=ErrorType.PERMANENT),
    ):
        await trader.run_forever()

    assert len(disabled_calls) == 1
    assert disabled_calls[0] == CB_FAILURE_THRESHOLD


@pytest.mark.unit
@pytest.mark.asyncio
async def test_do_step_transient_error_does_not_trip_breaker(trader):
    """Transient errors increment consecutive_failures but reset to 0 on next success."""
    import contextlib
    from unittest.mock import patch

    account = _make_account_mock()
    combo = _make_combo_mock(trader.account_id)
    extras, patches = _build_step_mocks(trader, account, [combo])

    call_count = 0
    original_do_step = trader._do_step

    async def _failing_then_succeeding():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("transient network error")
        trader._running = False
        return await original_do_step()

    sleep_calls = []

    async def _fake_sleep(secs):
        sleep_calls.append(secs)

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch.object(trader, "step", side_effect=_failing_then_succeeding))
        stack.enter_context(patch.object(trader, "_init_client", AsyncMock()))
        stack.enter_context(patch("app.services.account_trader.asyncio.sleep", side_effect=_fake_sleep))
        await trader.run_forever()

    assert trader._consecutive_failures == 0
    assert trader._failure_history == []
    assert len(sleep_calls) >= 1
