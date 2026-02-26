from __future__ import annotations
import logging
from aiolimiter import AsyncLimiter

logger = logging.getLogger(__name__)


class GlobalRateLimiter:
    """
    Binance API 글로벌 레이트 리미터.
    Binance 제한: 1200 request weight / minute
    안전 마진: 1000 weight / minute (83% 사용률)
    """

    def __init__(self, max_rate: int = 1000, time_period: float = 60.0):
        self._limiter = AsyncLimiter(max_rate=max_rate, time_period=time_period)
        self._max_rate = max_rate

    async def acquire(self, weight: int = 1):
        """weight만큼의 API 요청 용량을 확보. 초과 시 자동 대기."""
        for _ in range(weight):
            await self._limiter.acquire()

    @property
    def max_rate(self) -> int:
        return self._max_rate
