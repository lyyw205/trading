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


class CircuitBreaker:
    """
    계정별 서킷 브레이커.
    - CLOSED: 정상 동작
    - OPEN: MAX_FAILURES회 연속 실패 -> 계정 비활성화
    - 수동 리셋 필요 (관리자 대시보드에서)
    """
    MAX_FAILURES = 5

    def __init__(self):
        self._consecutive_failures = 0

    def record_success(self):
        self._consecutive_failures = 0

    def record_failure(self):
        self._consecutive_failures += 1

    @property
    def is_open(self) -> bool:
        return self._consecutive_failures >= self.MAX_FAILURES

    @property
    def failures(self) -> int:
        return self._consecutive_failures

    def reset(self):
        self._consecutive_failures = 0
