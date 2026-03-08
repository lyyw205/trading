"""Error classification for circuit breaker decisions."""

from __future__ import annotations

import enum
import logging

logger = logging.getLogger(__name__)


class ErrorType(enum.StrEnum):
    TRANSIENT = "transient"  # retry-able (system/infra) → CB count
    PERMANENT = "permanent"  # immediate CB trip (auth/key)
    RATE_LIMIT = "rate_limit"  # long backoff then retry, no CB count
    BALANCE = "balance"  # funds-related → buy-pause, no CB count


def classify_error(exc: Exception) -> ErrorType:
    """Classify exception to determine retry strategy.

    - TRANSIENT: network/timeout/server errors → normal backoff + CB count
    - PERMANENT: auth/key errors → immediate CB trip
    - RATE_LIMIT: too many requests → long backoff, no CB count
    - BALANCE: insufficient funds/notional → buy-pause route, no CB count
    """
    # Check Binance API exceptions
    try:
        from binance.exceptions import BinanceAPIException

        if isinstance(exc, BinanceAPIException):
            if exc.code in (-1015,):  # Too many requests
                return ErrorType.RATE_LIMIT
            if exc.code in (-2015, -2014, -2008):  # Invalid API key/permissions
                return ErrorType.PERMANENT
            if exc.code in (-2010, -2011, -1013):  # Insufficient balance, cancel rejected, filter failure
                return ErrorType.BALANCE
            if exc.code in (-1001, -1003, -1006, -1007):  # Network/server issues
                return ErrorType.TRANSIENT
    except ImportError:
        pass

    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return ErrorType.TRANSIENT

    # Check for balance-related error messages (non-Binance exceptions)
    msg = str(exc).lower()
    if any(kw in msg for kw in ("insufficient", "not enough", "below minimum", "min notional")):
        return ErrorType.BALANCE

    return ErrorType.TRANSIENT  # default: assume recoverable
