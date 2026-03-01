from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.exchange.base_client import ExchangeClient
    from app.services.kline_ws_manager import KlineWsManager

logger = logging.getLogger(__name__)


class PriceCollector:
    """
    심볼별 전역 가격 수집기.
    여러 계정이 동일 심볼을 사용해도 가격 조회는 1회만 수행.
    TradingEngine이 소유하고 각 AccountTrader에 주입.

    Price priority: WebSocket → in-memory cache → REST fallback.
    Candle/snapshot storage removed — handled by KlineWsManager.
    """

    def __init__(self):
        self._prices: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._exchange_clients: dict[str, ExchangeClient] = {}
        self._kline_ws: KlineWsManager | None = None

    def set_kline_ws(self, kline_ws: KlineWsManager) -> None:
        """Inject KlineWsManager reference (called by TradingEngine)."""
        self._kline_ws = kline_ws

    def register_client(self, symbol: str, client: ExchangeClient):
        """Register an exchange client for a symbol (first one wins)"""
        if symbol not in self._exchange_clients:
            self._exchange_clients[symbol] = client

    async def get_price(self, symbol: str) -> float:
        """Return price with fallback chain: WS → cache → REST.

        1. Try WebSocket latest price (real-time, sub-second)
        2. Fall back to in-memory cache (from previous REST calls)
        3. Fall back to REST API call (slowest)
        """
        # 1. WebSocket price (most up-to-date)
        if self._kline_ws:
            ws_price = self._kline_ws.get_latest_price(symbol)
            if ws_price and ws_price > 0:
                self._prices[symbol] = ws_price  # update cache too
                return ws_price

        # 2. In-memory cache
        if symbol in self._prices and self._prices[symbol] > 0:
            return self._prices[symbol]

        # 3. REST fallback
        return await self.refresh_symbol(symbol)

    async def refresh_symbol(self, symbol: str) -> float:
        """Refresh price for a single symbol via REST"""
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
