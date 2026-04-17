"""Rate limiter configuration test."""
from __future__ import annotations

import pytest


@pytest.mark.unit
class TestRateLimiterConfig:
    def test_default_limit_120_per_minute(self):
        """Global rate limiter default is 120/minute."""
        from app.dependencies import limiter
        assert limiter._default_limits is not None
        assert len(limiter._default_limits) > 0
        # LimitGroup is iterable; each item exposes .limit with .amount
        first_group = limiter._default_limits[0]
        limits = list(first_group)
        assert len(limits) == 1
        assert limits[0].limit.amount == 120
