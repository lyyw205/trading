from __future__ import annotations
import asyncio
import logging
import time
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.price_repo import insert_snapshot, upsert_candle_5m

if TYPE_CHECKING:
    from app.exchange.base_client import ExchangeClient

logger = logging.getLogger(__name__)


class PriceCollector:
    """
    심볼별 전역 가격 수집기.
    여러 계정이 동일 심볼을 사용해도 가격 조회는 1회만 수행.
    TradingEngine이 소유하고 각 AccountTrader에 주입.
    """

    def __init__(self):
        self._prices: dict[str, float] = {}
        self._last_snapshot_bucket: dict[str, int] = {}
        self._last_candle_bucket: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._exchange_clients: dict[str, ExchangeClient] = {}

    def register_client(self, symbol: str, client: ExchangeClient):
        """Register an exchange client for a symbol (first one wins)"""
        if symbol not in self._exchange_clients:
            self._exchange_clients[symbol] = client

    async def get_price(self, symbol: str) -> float:
        """Return cached price. Refresh if not available."""
        if symbol in self._prices:
            return self._prices[symbol]
        await self.refresh_symbol(symbol)
        return self._prices.get(symbol, 0.0)

    async def refresh_symbol(self, symbol: str) -> float:
        """Refresh price for a single symbol"""
        client = self._exchange_clients.get(symbol)
        if not client:
            return 0.0
        try:
            price = await client.get_price(symbol)
            self._prices[symbol] = price
            return price
        except Exception as e:
            logger.warning(f"Price fetch failed for {symbol}: {e}")
            return self._prices.get(symbol, 0.0)

    async def refresh_all(self) -> dict[str, float]:
        """Refresh all registered symbols in parallel"""
        symbols = list(self._exchange_clients.keys())
        if not symbols:
            return {}
        tasks = [self.refresh_symbol(s) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {s: r for s, r in zip(symbols, results) if isinstance(r, float)}

    async def maybe_store_snapshot(self, symbol: str, price: float, session: AsyncSession) -> None:
        """5분 버킷 기준으로 중복 없이 스냅샷 저장"""
        if price <= 0:
            return
        bucket = int(time.time() // 300)
        if self._last_snapshot_bucket.get(symbol) == bucket:
            return
        ts_ms = bucket * 300 * 1000
        await insert_snapshot(symbol=symbol, ts_ms=ts_ms, price=price, session=session)
        self._last_snapshot_bucket[symbol] = bucket

    async def maybe_store_candle(self, symbol: str, price: float, session: AsyncSession) -> None:
        """5분 버킷 기준으로 캔들 upsert"""
        if price <= 0:
            return
        bucket = int(time.time() // 300)
        ts_ms = bucket * 300 * 1000
        await upsert_candle_5m(symbol=symbol, ts_ms=ts_ms, price=price, session=session)
        if self._last_candle_bucket.get(symbol) != bucket:
            self._last_candle_bucket[symbol] = bucket
