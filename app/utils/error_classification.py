"""Error classification for circuit breaker decisions."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ErrorType:
    TRANSIENT = "transient"    # retry-able
    PERMANENT = "permanent"    # immediate CB trip
    RATE_LIMIT = "rate_limit"  # long backoff then retry


def classify_error(exc: Exception) -> str:
    """Classify exception to determine retry strategy.

    - TRANSIENT: network/timeout/server errors → normal backoff + CB count
    - PERMANENT: auth/key errors → immediate CB trip
    - RATE_LIMIT: too many requests → long backoff, no CB count
    """
    # Check Binance API exceptions
    try:
        from binance.exceptions import BinanceAPIException
        if isinstance(exc, BinanceAPIException):
            if exc.code in (-1015,):  # Too many requests
                return ErrorType.RATE_LIMIT
            if exc.code in (-2015, -2014, -2008):  # Invalid API key/permissions
                return ErrorType.PERMANENT
            if exc.code in (-1001, -1003, -1006, -1007):  # Network/server issues
                return ErrorType.TRANSIENT
    except ImportError:
        pass

    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return ErrorType.TRANSIENT

    return ErrorType.TRANSIENT  # default: assume recoverable
