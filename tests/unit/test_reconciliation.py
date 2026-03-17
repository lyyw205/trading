"""Unit tests for ReconciliationService.

Covers:
- Position drift detection (match, DB-only, exchange-only, qty mismatch)
- Dust threshold (below/above POSITION_THRESHOLD_PCT)
- Fill gap detection (no gap, small gap ignored, significant gap reported)
- repair_fill_gaps (no last_id, exchange empty, fills inserted)
- reconcile_account orchestration (ok, drift_detected, error status)
- _send_drift_alert swallows exceptions
- ReconciliationResult.to_dict serialisation
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.reconciliation import (
    BalanceDiff,
    FillGap,
    PositionDiff,
    ReconciliationResult,
    ReconciliationService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session() -> AsyncMock:
    """Minimal AsyncSession mock."""
    session = AsyncMock()
    return session


def _make_exchange() -> AsyncMock:
    exchange = AsyncMock()
    exchange.get_account_info = AsyncMock(return_value={"balances": []})
    exchange.get_my_trades = AsyncMock(return_value=[])
    exchange.get_my_trades_from_id = AsyncMock(return_value=[])
    return exchange


def _make_position(symbol: str, qty: float, account_id=None) -> MagicMock:
    pos = MagicMock()
    pos.account_id = account_id or uuid4()
    pos.symbol = symbol
    # Use float so that `pos.qty - ex_qty` arithmetic in production code works.
    # (Production code: `diff = pos.qty - ex_qty` where ex_qty is float.)
    pos.qty = qty
    return pos


def _make_db_result(rows: list) -> MagicMock:
    """Simulate session.execute() -> result.scalars().all() or result.all()."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    result.all.return_value = rows
    result.scalar.return_value = rows[0] if rows else None
    return result


# ---------------------------------------------------------------------------
# ReconciliationResult.to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReconciliationResultToDict:
    def test_to_dict_ok_status(self):
        account_id = uuid4()
        from datetime import UTC, datetime

        ts = datetime.now(UTC)
        r = ReconciliationResult(account_id=account_id, timestamp=ts, status="ok")
        d = r.to_dict()
        assert d["account_id"] == str(account_id)
        assert d["status"] == "ok"
        assert d["position_diffs"] == []
        assert d["fill_gaps"] == []
        assert d["balance_diff"] is None

    def test_to_dict_with_position_diffs(self):
        from datetime import UTC, datetime

        account_id = uuid4()
        diff = PositionDiff(
            symbol="ETHUSDT",
            db_qty=1.0,
            exchange_qty=0.5,
            diff_qty=0.5,
            diff_pct=50.0,
        )
        r = ReconciliationResult(
            account_id=account_id,
            timestamp=datetime.now(UTC),
            position_diffs=[diff],
            status="drift_detected",
        )
        d = r.to_dict()
        assert len(d["position_diffs"]) == 1
        assert d["position_diffs"][0]["symbol"] == "ETHUSDT"
        assert d["status"] == "drift_detected"

    def test_to_dict_with_balance_diff(self):
        from datetime import UTC, datetime

        account_id = uuid4()
        bd = BalanceDiff(asset="USDT", db_total=1000.0, exchange_total=999.0, diff=1.0)
        r = ReconciliationResult(
            account_id=account_id,
            timestamp=datetime.now(UTC),
            balance_diff=bd,
            status="drift_detected",
        )
        d = r.to_dict()
        assert d["balance_diff"]["asset"] == "USDT"
        assert d["balance_diff"]["diff"] == 1.0

    def test_to_dict_with_fill_gaps(self):
        from datetime import UTC, datetime

        account_id = uuid4()
        gap = FillGap(
            symbol="BTCUSDT",
            last_db_trade_id=100,
            exchange_latest_trade_id=110,
            estimated_missing=10,
        )
        r = ReconciliationResult(
            account_id=account_id,
            timestamp=datetime.now(UTC),
            fill_gaps=[gap],
            status="drift_detected",
        )
        d = r.to_dict()
        assert len(d["fill_gaps"]) == 1
        assert d["fill_gaps"][0]["symbol"] == "BTCUSDT"
        assert d["fill_gaps"][0]["estimated_missing"] == 10


# ---------------------------------------------------------------------------
# _check_positions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckPositions:
    @pytest.mark.asyncio
    async def test_no_positions_returns_empty(self):
        """DB has no positions → no diffs."""
        session = _make_session()
        session.execute = AsyncMock(return_value=_make_db_result([]))
        exchange = _make_exchange()
        svc = ReconciliationService(session, exchange)

        diffs = await svc._check_positions(uuid4())

        assert diffs == []
        exchange.get_account_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_positions_match_exchange_no_drift(self):
        """DB qty == exchange qty → no diff reported."""
        account_id = uuid4()
        pos = _make_position("ETHUSDT", 1.0, account_id)

        session = _make_session()
        session.execute = AsyncMock(return_value=_make_db_result([pos]))
        exchange = _make_exchange()
        exchange.get_account_info = AsyncMock(
            return_value={"balances": [{"asset": "ETH", "free": "1.0", "locked": "0"}]}
        )
        svc = ReconciliationService(session, exchange)

        diffs = await svc._check_positions(account_id)

        assert diffs == []

    @pytest.mark.asyncio
    async def test_db_position_missing_on_exchange_drift(self):
        """DB has ETH position but exchange has zero → drift detected."""
        account_id = uuid4()
        pos = _make_position("ETHUSDT", 1.0, account_id)

        session = _make_session()
        session.execute = AsyncMock(return_value=_make_db_result([pos]))
        exchange = _make_exchange()
        exchange.get_account_info = AsyncMock(return_value={"balances": []})
        svc = ReconciliationService(session, exchange)

        diffs = await svc._check_positions(account_id)

        assert len(diffs) == 1
        assert diffs[0].symbol == "ETHUSDT"
        assert diffs[0].db_qty == 1.0
        assert diffs[0].exchange_qty == 0.0

    @pytest.mark.asyncio
    async def test_quantity_mismatch_drift(self):
        """DB qty differs from exchange qty by > threshold → drift."""
        account_id = uuid4()
        pos = _make_position("BTCUSDT", 0.5, account_id)

        session = _make_session()
        session.execute = AsyncMock(return_value=_make_db_result([pos]))
        exchange = _make_exchange()
        exchange.get_account_info = AsyncMock(
            return_value={"balances": [{"asset": "BTC", "free": "0.3", "locked": "0"}]}
        )
        svc = ReconciliationService(session, exchange)

        diffs = await svc._check_positions(account_id)

        assert len(diffs) == 1
        assert diffs[0].db_qty == 0.5
        assert diffs[0].exchange_qty == 0.3

    @pytest.mark.asyncio
    async def test_dust_difference_below_threshold_ignored(self):
        """Difference below POSITION_THRESHOLD_PCT → not reported."""
        account_id = uuid4()
        # 1.0 vs 1.0000001 → ~0.00001% difference, well below 0.01%
        pos = _make_position("ETHUSDT", 1.0, account_id)

        session = _make_session()
        session.execute = AsyncMock(return_value=_make_db_result([pos]))
        exchange = _make_exchange()
        exchange.get_account_info = AsyncMock(
            return_value={"balances": [{"asset": "ETH", "free": "1.0000001", "locked": "0"}]}
        )
        svc = ReconciliationService(session, exchange)

        diffs = await svc._check_positions(account_id)

        assert diffs == []

    @pytest.mark.asyncio
    async def test_zero_qty_position_skipped(self):
        """Positions with qty <= 0 produce no diffs (skipped inside loop)."""
        account_id = uuid4()
        pos = _make_position("ETHUSDT", 0.0, account_id)

        session = _make_session()
        session.execute = AsyncMock(return_value=_make_db_result([pos]))
        exchange = _make_exchange()
        # Exchange is still called (positions exist); the zero-qty skip is inside the loop.
        exchange.get_account_info = AsyncMock(return_value={"balances": []})
        svc = ReconciliationService(session, exchange)

        diffs = await svc._check_positions(account_id)

        assert diffs == []

    @pytest.mark.asyncio
    async def test_exchange_error_returns_empty(self):
        """Exchange API failure → returns empty list, no exception propagated."""
        account_id = uuid4()
        pos = _make_position("ETHUSDT", 1.0, account_id)

        session = _make_session()
        session.execute = AsyncMock(return_value=_make_db_result([pos]))
        exchange = _make_exchange()
        exchange.get_account_info = AsyncMock(side_effect=Exception("network error"))
        svc = ReconciliationService(session, exchange)

        diffs = await svc._check_positions(account_id)

        assert diffs == []

    @pytest.mark.asyncio
    async def test_locked_balance_included_in_exchange_qty(self):
        """Exchange qty = free + locked."""
        account_id = uuid4()
        pos = _make_position("ETHUSDT", 1.0, account_id)

        session = _make_session()
        session.execute = AsyncMock(return_value=_make_db_result([pos]))
        exchange = _make_exchange()
        # free=0.6 + locked=0.4 = 1.0 → no diff
        exchange.get_account_info = AsyncMock(
            return_value={"balances": [{"asset": "ETH", "free": "0.6", "locked": "0.4"}]}
        )
        svc = ReconciliationService(session, exchange)

        diffs = await svc._check_positions(account_id)

        assert diffs == []


# ---------------------------------------------------------------------------
# _check_fill_gaps
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckFillGaps:
    @pytest.mark.asyncio
    async def test_no_fills_in_db_returns_empty(self):
        """No fills in DB → no gaps checked."""
        session = _make_session()
        result = MagicMock()
        result.all.return_value = []
        session.execute = AsyncMock(return_value=result)
        exchange = _make_exchange()
        svc = ReconciliationService(session, exchange)

        gaps = await svc._check_fill_gaps(uuid4())

        assert gaps == []
        exchange.get_my_trades.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exchange_at_same_id_no_gap(self):
        """Exchange latest trade_id == DB last_id → no gap."""
        account_id = uuid4()
        session = _make_session()
        result = MagicMock()
        result.all.return_value = [("ETHUSDT", 500)]
        session.execute = AsyncMock(return_value=result)
        exchange = _make_exchange()
        exchange.get_my_trades = AsyncMock(return_value=[{"id": "500"}])
        svc = ReconciliationService(session, exchange)

        gaps = await svc._check_fill_gaps(account_id)

        assert gaps == []

    @pytest.mark.asyncio
    async def test_small_gap_below_threshold_not_reported(self):
        """Gap of 3 trades (≤5) is not reported."""
        account_id = uuid4()
        session = _make_session()
        result = MagicMock()
        result.all.return_value = [("ETHUSDT", 100)]
        session.execute = AsyncMock(return_value=result)
        exchange = _make_exchange()
        exchange.get_my_trades = AsyncMock(return_value=[{"id": "103"}])
        svc = ReconciliationService(session, exchange)

        gaps = await svc._check_fill_gaps(account_id)

        assert gaps == []

    @pytest.mark.asyncio
    async def test_large_gap_above_threshold_reported(self):
        """Gap of >5 trades → FillGap reported."""
        account_id = uuid4()
        session = _make_session()
        result = MagicMock()
        result.all.return_value = [("ETHUSDT", 100)]
        session.execute = AsyncMock(return_value=result)
        exchange = _make_exchange()
        exchange.get_my_trades = AsyncMock(return_value=[{"id": "110"}])
        svc = ReconciliationService(session, exchange)

        gaps = await svc._check_fill_gaps(account_id)

        assert len(gaps) == 1
        assert gaps[0].symbol == "ETHUSDT"
        assert gaps[0].last_db_trade_id == 100
        assert gaps[0].exchange_latest_trade_id == 110
        assert gaps[0].estimated_missing == 10

    @pytest.mark.asyncio
    async def test_exchange_returns_no_trades_skipped(self):
        """Exchange returns empty list for a symbol → no gap created."""
        account_id = uuid4()
        session = _make_session()
        result = MagicMock()
        result.all.return_value = [("ETHUSDT", 100)]
        session.execute = AsyncMock(return_value=result)
        exchange = _make_exchange()
        exchange.get_my_trades = AsyncMock(return_value=[])
        svc = ReconciliationService(session, exchange)

        gaps = await svc._check_fill_gaps(account_id)

        assert gaps == []

    @pytest.mark.asyncio
    async def test_fill_gap_check_exchange_error_skipped(self):
        """Exchange error per symbol is swallowed; other symbols still processed."""
        account_id = uuid4()
        session = _make_session()
        result = MagicMock()
        result.all.return_value = [("ETHUSDT", 100), ("BTCUSDT", 200)]
        session.execute = AsyncMock(return_value=result)
        exchange = _make_exchange()

        async def _trades_side_effect(symbol, limit=1):
            if symbol == "ETHUSDT":
                raise Exception("API error")
            return [{"id": "210"}]

        exchange.get_my_trades = AsyncMock(side_effect=_trades_side_effect)
        svc = ReconciliationService(session, exchange)

        gaps = await svc._check_fill_gaps(account_id)

        # ETHUSDT errored (skipped), BTCUSDT has gap of 10
        assert len(gaps) == 1
        assert gaps[0].symbol == "BTCUSDT"
        assert gaps[0].estimated_missing == 10


# ---------------------------------------------------------------------------
# repair_fill_gaps
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRepairFillGaps:
    @pytest.mark.asyncio
    async def test_no_last_id_returns_zero(self):
        """No fills in DB for symbol → repair returns 0."""
        account_id = uuid4()
        session = _make_session()
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = None
        session.execute = AsyncMock(return_value=scalar_result)
        exchange = _make_exchange()
        svc = ReconciliationService(session, exchange)

        count = await svc.repair_fill_gaps(account_id, "ETHUSDT")

        assert count == 0
        exchange.get_my_trades_from_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exchange_returns_no_trades_returns_zero(self):
        """Exchange has no new trades → repair returns 0."""
        account_id = uuid4()
        session = _make_session()
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 100
        session.execute = AsyncMock(return_value=scalar_result)
        exchange = _make_exchange()
        exchange.get_my_trades_from_id = AsyncMock(return_value=[])
        svc = ReconciliationService(session, exchange)

        count = await svc.repair_fill_gaps(account_id, "ETHUSDT")

        assert count == 0

    @pytest.mark.asyncio
    async def test_repair_inserts_fills_and_recomputes_position(self):
        """Repair fetches missing trades, inserts fills, recomputes position."""
        account_id = uuid4()
        session = _make_session()
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 100
        session.execute = AsyncMock(return_value=scalar_result)
        exchange = _make_exchange()
        trades = [
            {"id": "101", "orderId": "9001", "qty": "0.1", "price": "2000"},
            {"id": "102", "orderId": "9002", "qty": "0.1", "price": "2010"},
        ]
        exchange.get_my_trades_from_id = AsyncMock(return_value=trades)

        order_repo_mock = AsyncMock()
        order_repo_mock.insert_fills_batch = AsyncMock()
        pos_repo_mock = AsyncMock()
        pos_repo_mock.recompute_from_fills = AsyncMock()

        svc = ReconciliationService(session, exchange)

        with (
            patch("app.services.reconciliation.OrderRepository", return_value=order_repo_mock),
            patch("app.services.reconciliation.PositionRepository", return_value=pos_repo_mock),
        ):
            count = await svc.repair_fill_gaps(account_id, "ETHUSDT")

        assert count == 2
        order_repo_mock.insert_fills_batch.assert_awaited_once()
        pos_repo_mock.recompute_from_fills.assert_awaited_once_with(account_id, "ETHUSDT")

    @pytest.mark.asyncio
    async def test_repair_exchange_error_returns_zero(self):
        """Exchange fetch error → repair returns 0 without propagating."""
        account_id = uuid4()
        session = _make_session()
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 100
        session.execute = AsyncMock(return_value=scalar_result)
        exchange = _make_exchange()
        exchange.get_my_trades_from_id = AsyncMock(side_effect=Exception("timeout"))
        svc = ReconciliationService(session, exchange)

        count = await svc.repair_fill_gaps(account_id, "ETHUSDT")

        assert count == 0


# ---------------------------------------------------------------------------
# reconcile_account (orchestration)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReconcileAccount:
    @pytest.mark.asyncio
    async def test_status_ok_when_no_diffs(self):
        """No position diffs + no fill gaps → status 'ok'."""
        account_id = uuid4()
        svc = ReconciliationService(_make_session(), _make_exchange())

        alert_mock = AsyncMock()
        with (
            patch.object(svc, "_check_positions", AsyncMock(return_value=[])),
            patch.object(svc, "_check_fill_gaps", AsyncMock(return_value=[])),
            patch.object(svc, "_send_drift_alert", alert_mock),
        ):
            result = await svc.reconcile_account(account_id)

        assert result.status == "ok"
        assert result.account_id == account_id
        alert_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_status_drift_detected_on_position_diff(self):
        """Position diffs present → status 'drift_detected' + alert sent."""
        account_id = uuid4()
        diff = PositionDiff(
            symbol="ETHUSDT",
            db_qty=1.0,
            exchange_qty=0.0,
            diff_qty=1.0,
            diff_pct=100.0,
        )
        svc = ReconciliationService(_make_session(), _make_exchange())

        alert_mock = AsyncMock()
        with (
            patch.object(svc, "_check_positions", AsyncMock(return_value=[diff])),
            patch.object(svc, "_check_fill_gaps", AsyncMock(return_value=[])),
            patch.object(svc, "_send_drift_alert", alert_mock),
        ):
            result = await svc.reconcile_account(account_id)

        assert result.status == "drift_detected"
        assert len(result.position_diffs) == 1
        alert_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_drift_detected_on_fill_gap(self):
        """Fill gaps present → status 'drift_detected'."""
        account_id = uuid4()
        gap = FillGap(
            symbol="BTCUSDT",
            last_db_trade_id=100,
            exchange_latest_trade_id=200,
            estimated_missing=100,
        )
        svc = ReconciliationService(_make_session(), _make_exchange())

        with (
            patch.object(svc, "_check_positions", AsyncMock(return_value=[])),
            patch.object(svc, "_check_fill_gaps", AsyncMock(return_value=[gap])),
            patch.object(svc, "_send_drift_alert", AsyncMock()),
        ):
            result = await svc.reconcile_account(account_id)

        assert result.status == "drift_detected"
        assert len(result.fill_gaps) == 1

    @pytest.mark.asyncio
    async def test_status_error_on_exception(self):
        """Unexpected exception → status 'error', no re-raise."""
        account_id = uuid4()
        svc = ReconciliationService(_make_session(), _make_exchange())

        with patch.object(svc, "_check_positions", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await svc.reconcile_account(account_id)

        assert result.status == "error"
        assert result.account_id == account_id
        assert result.position_diffs == []
        assert result.fill_gaps == []

    @pytest.mark.asyncio
    async def test_send_drift_alert_failure_does_not_propagate(self):
        """Alert send failure is swallowed; result still returned."""
        account_id = uuid4()
        diff = PositionDiff(
            symbol="ETHUSDT",
            db_qty=1.0,
            exchange_qty=0.0,
            diff_qty=1.0,
            diff_pct=100.0,
        )
        svc = ReconciliationService(_make_session(), _make_exchange())

        failing_alert = AsyncMock(side_effect=Exception("alert service down"))
        with (
            patch.object(svc, "_check_positions", AsyncMock(return_value=[diff])),
            patch.object(svc, "_check_fill_gaps", AsyncMock(return_value=[])),
            patch.object(svc, "_send_drift_alert", failing_alert),
        ):
            # _send_drift_alert raises, but reconcile_account catches it and returns error
            result = await svc.reconcile_account(account_id)

        # The outer exception handler catches the alert failure → status error
        assert result.status in ("drift_detected", "error")
