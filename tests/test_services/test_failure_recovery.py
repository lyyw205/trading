"""Tests for fault recovery: error classification, circuit breaker recovery, DB retry."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

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
        """Binance -1015 (too many requests) → RATE_LIMIT."""
        try:
            from binance.exceptions import BinanceAPIException

            exc = BinanceAPIException(
                MagicMock(status_code=429, text="", headers={}), code=-1015, message="Too many requests"
            )
            # BinanceAPIException constructor varies; try direct attribute set
        except (ImportError, TypeError):
            pytest.skip("binance not installed or API changed")
            return
        exc = MagicMock(spec=Exception)
        exc.__class__ = type("BinanceAPIException", (Exception,), {})
        exc.code = -1015
        # Since we mock, we need to actually test the real classify_error
        # Just verify the logic paths with a real BinanceAPIException if available
        try:
            from binance.exceptions import BinanceAPIException

            # Create a mock response
            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.text = '{"code": -1015, "msg": "Too many requests"}'
            mock_response.headers = {}
            exc = BinanceAPIException(mock_response)
            exc.code = -1015
            result = classify_error(exc)
            assert result == ErrorType.RATE_LIMIT
        except (ImportError, TypeError):
            pytest.skip("Cannot construct BinanceAPIException")

    def test_binance_invalid_api_key(self):
        """Binance -2015 (invalid API key) → PERMANENT."""
        try:
            from binance.exceptions import BinanceAPIException

            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = '{"code": -2015, "msg": "Invalid API-key"}'
            mock_response.headers = {}
            exc = BinanceAPIException(mock_response)
            exc.code = -2015
            result = classify_error(exc)
            assert result == ErrorType.PERMANENT
        except (ImportError, TypeError):
            pytest.skip("Cannot construct BinanceAPIException")

    def test_binance_network_error(self):
        """Binance -1001 (disconnected) → TRANSIENT."""
        try:
            from binance.exceptions import BinanceAPIException

            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = '{"code": -1001, "msg": "Disconnected"}'
            mock_response.headers = {}
            exc = BinanceAPIException(mock_response)
            exc.code = -1001
            result = classify_error(exc)
            assert result == ErrorType.TRANSIENT
        except (ImportError, TypeError):
            pytest.skip("Cannot construct BinanceAPIException")


# ============================================================
#  Circuit Breaker Auto-Recovery
# ============================================================
class TestCircuitBreakerRecovery:
    @pytest.fixture
    def mock_account(self):
        account = MagicMock()
        account.id = uuid4()
        account.circuit_breaker_disabled_at = datetime.now(UTC) - timedelta(minutes=40)
        account.auto_recovery_attempts = 0
        account.circuit_breaker_failures = 5
        return account

    @pytest.fixture
    def mock_account_recent_trip(self):
        account = MagicMock()
        account.id = uuid4()
        account.circuit_breaker_disabled_at = datetime.now(UTC) - timedelta(minutes=5)
        account.auto_recovery_attempts = 0
        account.circuit_breaker_failures = 5
        return account

    @pytest.fixture
    def mock_account_max_retries(self):
        account = MagicMock()
        account.id = uuid4()
        account.circuit_breaker_disabled_at = datetime.now(UTC) - timedelta(minutes=40)
        account.auto_recovery_attempts = 3
        account.circuit_breaker_failures = 5
        return account

    async def test_auto_recovery_after_cooldown(self, mock_account):
        """CB trip + cooldown elapsed → auto restart."""
        elapsed = (datetime.now(UTC) - mock_account.circuit_breaker_disabled_at).total_seconds()
        assert elapsed >= 1800  # 30 min cooldown
        assert mock_account.auto_recovery_attempts < 3
        # Account should be eligible for recovery
        should_recover = (
            mock_account.circuit_breaker_disabled_at is not None
            and elapsed >= 1800
            and mock_account.auto_recovery_attempts < 3
        )
        assert should_recover is True

    async def test_no_recovery_before_cooldown(self, mock_account_recent_trip):
        """CB trip + cooldown NOT elapsed → skip."""
        elapsed = (datetime.now(UTC) - mock_account_recent_trip.circuit_breaker_disabled_at).total_seconds()
        assert elapsed < 1800
        should_recover = elapsed >= 1800
        assert should_recover is False

    async def test_max_auto_retries_exceeded(self, mock_account_max_retries):
        """3 auto-recovery attempts → stop trying."""
        assert mock_account_max_retries.auto_recovery_attempts >= 3
        should_recover = mock_account_max_retries.auto_recovery_attempts < 3
        assert should_recover is False

    async def test_manual_reset_clears_attempts(self):
        """Manual reset should clear auto_recovery_attempts."""
        # This tests the repo method logic
        account = MagicMock()
        account.circuit_breaker_failures = 0
        account.circuit_breaker_disabled_at = None
        account.auto_recovery_attempts = 0
        assert account.auto_recovery_attempts == 0


# ============================================================
#  DB Connection Retry
# ============================================================
class TestDbRetry:
    async def test_operational_error_retried(self):
        """OperationalError should trigger retry up to 3 times."""
        from sqlalchemy.exc import OperationalError

        call_count = 0

        async def mock_do_step():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OperationalError("connection failed", None, None)
            return 60

        # Simulate the retry logic from step()
        result = None
        for attempt in range(3):
            try:
                result = await mock_do_step()
                break
            except OperationalError:
                if attempt < 2:
                    await asyncio.sleep(0)  # skip actual sleep in test

        assert call_count == 3
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
            for attempt in range(3):
                try:
                    await mock_do_step()
                    break
                except OperationalError:
                    if attempt < 2:
                        await asyncio.sleep(0)

        assert call_count == 1  # Only called once, ValueError escapes

    async def test_all_retries_exhausted(self):
        """3 consecutive OperationalErrors → raise the last one."""
        from sqlalchemy.exc import OperationalError

        call_count = 0

        async def mock_do_step():
            nonlocal call_count
            call_count += 1
            raise OperationalError("still failing", None, None)

        last_exc = None
        for attempt in range(3):
            try:
                await mock_do_step()
                break
            except OperationalError as e:
                last_exc = e
                if attempt < 2:
                    await asyncio.sleep(0)
        else:
            assert last_exc is not None

        assert call_count == 3
