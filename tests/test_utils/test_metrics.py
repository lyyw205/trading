"""Unit tests for Prometheus metrics definitions."""

import pytest
from prometheus_client import Counter, Gauge, Histogram


@pytest.mark.unit
class TestMetricDefinitions:
    """Verify all metric objects are properly defined."""

    def test_trading_cycle_duration_is_histogram(self):
        from app.utils.metrics import TRADING_CYCLE_DURATION

        assert isinstance(TRADING_CYCLE_DURATION, Histogram)

    def test_order_placement_duration_is_histogram(self):
        from app.utils.metrics import ORDER_PLACEMENT_DURATION

        assert isinstance(ORDER_PLACEMENT_DURATION, Histogram)

    def test_orders_placed_is_counter(self):
        from app.utils.metrics import ORDERS_PLACED

        assert isinstance(ORDERS_PLACED, Counter)

    def test_circuit_breaker_trips_is_counter(self):
        from app.utils.metrics import CIRCUIT_BREAKER_TRIPS

        assert isinstance(CIRCUIT_BREAKER_TRIPS, Counter)

    def test_buy_pause_state_is_gauge(self):
        from app.utils.metrics import BUY_PAUSE_STATE

        assert isinstance(BUY_PAUSE_STATE, Gauge)

    def test_ws_messages_received_is_counter(self):
        from app.utils.metrics import WS_MESSAGES_RECEIVED

        assert isinstance(WS_MESSAGES_RECEIVED, Counter)

    def test_ws_reconnections_is_counter(self):
        from app.utils.metrics import WS_RECONNECTIONS

        assert isinstance(WS_RECONNECTIONS, Counter)

    def test_balance_usdt_is_gauge(self):
        from app.utils.metrics import BALANCE_USDT

        assert isinstance(BALANCE_USDT, Gauge)

    def test_threadpool_utilization_is_gauge(self):
        from app.utils.metrics import THREADPOOL_UTILIZATION

        assert isinstance(THREADPOOL_UTILIZATION, Gauge)

    def test_recon_drift_is_counter(self):
        from app.utils.metrics import RECON_DRIFT

        assert isinstance(RECON_DRIFT, Counter)

    def test_recon_runs_is_counter(self):
        from app.utils.metrics import RECON_RUNS

        assert isinstance(RECON_RUNS, Counter)

    def test_active_accounts_is_gauge(self):
        from app.utils.metrics import ACTIVE_ACCOUNTS

        assert isinstance(ACTIVE_ACCOUNTS, Gauge)

    def test_open_lots_is_gauge(self):
        from app.utils.metrics import OPEN_LOTS

        assert isinstance(OPEN_LOTS, Gauge)

    def test_exchange_api_calls_is_counter(self):
        from app.utils.metrics import EXCHANGE_API_CALLS

        assert isinstance(EXCHANGE_API_CALLS, Counter)

    def test_exchange_api_errors_is_counter(self):
        from app.utils.metrics import EXCHANGE_API_ERRORS

        assert isinstance(EXCHANGE_API_ERRORS, Counter)

    def test_db_retry_attempts_is_counter(self):
        from app.utils.metrics import DB_RETRY_ATTEMPTS

        assert isinstance(DB_RETRY_ATTEMPTS, Counter)

    def test_auto_recovery_attempts_is_counter(self):
        from app.utils.metrics import AUTO_RECOVERY_ATTEMPTS

        assert isinstance(AUTO_RECOVERY_ATTEMPTS, Counter)

    def test_all_metrics_importable(self):
        """All metrics can be imported from the module in one go."""
        from app.utils.metrics import (
            ACTIVE_ACCOUNTS,
            AUTO_RECOVERY_ATTEMPTS,
            BALANCE_USDT,
            BUY_PAUSE_STATE,
            CIRCUIT_BREAKER_TRIPS,
            DB_RETRY_ATTEMPTS,
            EXCHANGE_API_CALLS,
            EXCHANGE_API_ERRORS,
            OPEN_LOTS,
            ORDER_PLACEMENT_DURATION,
            ORDERS_PLACED,
            RECON_DRIFT,
            RECON_RUNS,
            THREADPOOL_UTILIZATION,
            TRADING_CYCLE_DURATION,
            WS_MESSAGES_RECEIVED,
            WS_RECONNECTIONS,
        )

        metrics = [
            TRADING_CYCLE_DURATION,
            ORDER_PLACEMENT_DURATION,
            ORDERS_PLACED,
            CIRCUIT_BREAKER_TRIPS,
            BUY_PAUSE_STATE,
            WS_MESSAGES_RECEIVED,
            WS_RECONNECTIONS,
            BALANCE_USDT,
            THREADPOOL_UTILIZATION,
            RECON_DRIFT,
            RECON_RUNS,
            ACTIVE_ACCOUNTS,
            OPEN_LOTS,
            EXCHANGE_API_CALLS,
            EXCHANGE_API_ERRORS,
            DB_RETRY_ATTEMPTS,
            AUTO_RECOVERY_ATTEMPTS,
        ]
        assert all(m is not None for m in metrics)
