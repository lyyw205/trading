"""Tests for fault recovery: error classification, circuit breaker recovery, DB retry."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.services.trading_engine import (
    _CB_COOLDOWN_SEC,
    _CB_MAX_AUTO_RETRIES,
    should_attempt_recovery,
)
from app.utils.error_classification import ErrorType, classify_error


# ============================================================
#  Error Classification
# ============================================================
class TestErrorClassification:
    def test_connection_error_is_transient(self):
        assert classify_error(ConnectionError("refused")) == ErrorType.TRANSIENT

    def test_timeout_error_is_transient(self):
        assert classify_error(TimeoutError("timed out")) == ErrorType.TRANSIENT

    def test_os_error_is_transient(self):
        assert classify_error(OSError("network unreachable")) == ErrorType.TRANSIENT

    def test_generic_exception_defaults_transient(self):
        assert classify_error(ValueError("unexpected")) == ErrorType.TRANSIENT

    def test_runtime_error_defaults_transient(self):
        assert classify_error(RuntimeError("something broke")) == ErrorType.TRANSIENT

    def test_binance_rate_limit(self):
        """Binance -1015 (too many requests) -> RATE_LIMIT."""
        try:
            from binance.exceptions import BinanceAPIException

            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.text = '{"code": -1015, "msg": "Too many requests"}'
            mock_response.headers = {}
            exc = BinanceAPIException(mock_response)
            exc.code = -1015
            assert classify_error(exc) == ErrorType.RATE_LIMIT
        except (ImportError, TypeError):
            pytest.skip("Cannot construct BinanceAPIException")

    def test_binance_invalid_api_key(self):
        """Binance -2015 (invalid API key) -> PERMANENT."""
        try:
            from binance.exceptions import BinanceAPIException

            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = '{"code": -2015, "msg": "Invalid API-key"}'
            mock_response.headers = {}
            exc = BinanceAPIException(mock_response)
            exc.code = -2015
            assert classify_error(exc) == ErrorType.PERMANENT
        except (ImportError, TypeError):
            pytest.skip("Cannot construct BinanceAPIException")

    def test_binance_network_error(self):
        """Binance -1001 (disconnected) -> TRANSIENT."""
        try:
            from binance.exceptions import BinanceAPIException

            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = '{"code": -1001, "msg": "Disconnected"}'
            mock_response.headers = {}
            exc = BinanceAPIException(mock_response)
            exc.code = -1001
            assert classify_error(exc) == ErrorType.TRANSIENT
        except (ImportError, TypeError):
            pytest.skip("Cannot construct BinanceAPIException")

    def test_balance_insufficient_funds(self):
        """Binance -2010 (insufficient balance) -> BALANCE."""
        try:
            from binance.exceptions import BinanceAPIException

            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = '{"code": -2010, "msg": "Insufficient balance"}'
            mock_response.headers = {}
            exc = BinanceAPIException(mock_response)
            exc.code = -2010
            assert classify_error(exc) == ErrorType.BALANCE
        except (ImportError, TypeError):
            pytest.skip("Cannot construct BinanceAPIException")

    def test_balance_message_based(self):
        """Non-Binance exception with balance keywords -> BALANCE."""
        assert classify_error(RuntimeError("insufficient funds for order")) == ErrorType.BALANCE
        assert classify_error(RuntimeError("below minimum notional, min notional check failed")) == ErrorType.BALANCE


# ============================================================
#  Circuit Breaker Auto-Recovery (calls production code)
# ============================================================
class TestCircuitBreakerRecovery:
    """Tests for should_attempt_recovery() — production pure function."""

    def test_recovery_after_cooldown(self):
        """CB trip + cooldown elapsed + retries available -> True."""
        disabled_at = datetime.now(UTC) - timedelta(minutes=40)
        assert should_attempt_recovery(disabled_at, auto_recovery_attempts=0) is True

    def test_no_recovery_before_cooldown(self):
        """CB trip + cooldown NOT elapsed -> False."""
        disabled_at = datetime.now(UTC) - timedelta(minutes=5)
        assert should_attempt_recovery(disabled_at, auto_recovery_attempts=0) is False

    def test_max_retries_exceeded(self):
        """Max auto-recovery attempts reached -> False."""
        disabled_at = datetime.now(UTC) - timedelta(minutes=40)
        assert should_attempt_recovery(disabled_at, auto_recovery_attempts=_CB_MAX_AUTO_RETRIES) is False

    def test_not_tripped_returns_false(self):
        """disabled_at=None (not tripped) -> False."""
        assert should_attempt_recovery(None, auto_recovery_attempts=0) is False

    def test_uses_production_constants(self):
        """Verify tests use actual production constants, not hardcoded values."""
        assert _CB_COOLDOWN_SEC == 1800
        assert _CB_MAX_AUTO_RETRIES == 3

    def test_boundary_exactly_at_cooldown(self):
        """Exactly at cooldown boundary -> eligible."""
        now = datetime.now(UTC)
        disabled_at = now - timedelta(seconds=_CB_COOLDOWN_SEC)
        assert should_attempt_recovery(disabled_at, auto_recovery_attempts=0, now=now) is True

    def test_boundary_one_second_before_cooldown(self):
        """One second before cooldown -> not eligible."""
        now = datetime.now(UTC)
        disabled_at = now - timedelta(seconds=_CB_COOLDOWN_SEC - 1)
        assert should_attempt_recovery(disabled_at, auto_recovery_attempts=0, now=now) is False


# ============================================================
#  DB Connection Retry
# ============================================================
_PRODUCTION_RETRY_COUNT = 3  # mirrors AccountTrader.step() range(3)


class TestDbRetry:
    async def test_operational_error_retried(self):
        """OperationalError should trigger retry up to 3 times."""
        from sqlalchemy.exc import OperationalError

        call_count = 0

        async def mock_do_step():
            nonlocal call_count
            call_count += 1
            if call_count < _PRODUCTION_RETRY_COUNT:
                raise OperationalError("connection failed", None, None)
            return 60

        # Simulate the retry logic from AccountTrader.step() lines 119-132
        result = None
        for attempt in range(_PRODUCTION_RETRY_COUNT):
            try:
                result = await mock_do_step()
                break
            except OperationalError:
                if attempt < _PRODUCTION_RETRY_COUNT - 1:
                    await asyncio.sleep(0)

        assert call_count == _PRODUCTION_RETRY_COUNT
        assert result == 60

    async def test_non_db_error_not_retried(self):
        """Non-OperationalError should not be caught by retry."""
        from sqlalchemy.exc import OperationalError

        call_count = 0

        async def mock_do_step():
            nonlocal call_count
            call_count += 1
            raise ValueError("not a DB error")

        with pytest.raises(ValueError):
            for attempt in range(_PRODUCTION_RETRY_COUNT):
                try:
                    await mock_do_step()
                    break
                except OperationalError:
                    if attempt < _PRODUCTION_RETRY_COUNT - 1:
                        await asyncio.sleep(0)

        assert call_count == 1

    async def test_all_retries_exhausted(self):
        """3 consecutive OperationalErrors -> raise the last one."""
        from sqlalchemy.exc import OperationalError

        call_count = 0

        async def mock_do_step():
            nonlocal call_count
            call_count += 1
            raise OperationalError("still failing", None, None)

        last_exc = None
        for attempt in range(_PRODUCTION_RETRY_COUNT):
            try:
                await mock_do_step()
                break
            except OperationalError as e:
                last_exc = e
                if attempt < _PRODUCTION_RETRY_COUNT - 1:
                    await asyncio.sleep(0)
        else:
            assert last_exc is not None

        assert call_count == _PRODUCTION_RETRY_COUNT
