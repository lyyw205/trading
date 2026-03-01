"""
KlineWsManager — WebSocket-based real-time 1m kline collector.

Uses Binance WebSocket kline stream via multiplex_socket for all active symbols.
Stores completed 1m candles to DB and maintains latest price in memory.
Includes supervisor loop for fault recovery and REST backfill for gap filling.
"""
from __future__ import annotations

import asyncio
import logging

from app.db.session import TradingSessionLocal
from app.services.candle_store import store_candles_batch_1m, store_closed_candle_1m

logger = logging.getLogger(__name__)

# Supervisor backoff constants
_INITIAL_BACKOFF = 10
_MAX_BACKOFF = 300
_BACKOFF_FACTOR = 2


class KlineWsManager:
    """Centralized WebSocket kline subscription manager."""

    def __init__(self):
        self._subscriptions: dict[str, int] = {}  # symbol -> refcount
        self._latest_prices: dict[str, float] = {}
        self._async_client = None  # binance.AsyncClient
        self._bsm = None  # BinanceSocketManager
        self._ws_task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Initialize AsyncClient and start supervisor."""
        self._running = True
        try:
            from binance import AsyncClient
            self._async_client = await AsyncClient.create()
            logger.info("KlineWsManager: AsyncClient created (public streams)")
        except Exception as e:
            logger.error("KlineWsManager: Failed to create AsyncClient: %s", e)
            # Will retry in supervisor
            self._async_client = None

    async def stop(self) -> None:
        """Shut down all connections cleanly."""
        self._running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._async_client:
            try:
                await self._async_client.close_connection()
            except Exception:
                pass
        logger.info("KlineWsManager: stopped")

    async def subscribe(self, symbol: str) -> None:
        """Add a symbol subscription (refcount-based)."""
        async with self._lock:
            symbol_lower = symbol.lower()
            prev_count = self._subscriptions.get(symbol_lower, 0)
            self._subscriptions[symbol_lower] = prev_count + 1
            if prev_count == 0:
                logger.info("KlineWsManager: subscribed to %s (new)", symbol)
                await self._rebuild_multiplex()
            else:
                logger.debug("KlineWsManager: %s refcount -> %d", symbol, prev_count + 1)

    async def unsubscribe(self, symbol: str) -> None:
        """Remove a symbol subscription."""
        async with self._lock:
            symbol_lower = symbol.lower()
            count = self._subscriptions.get(symbol_lower, 0)
            if count <= 1:
                self._subscriptions.pop(symbol_lower, None)
                logger.info("KlineWsManager: unsubscribed from %s", symbol)
                await self._rebuild_multiplex()
            elif count > 1:
                self._subscriptions[symbol_lower] = count - 1
                logger.debug("KlineWsManager: %s refcount -> %d", symbol, count - 1)

    def get_latest_price(self, symbol: str) -> float | None:
        """Return the latest price from WebSocket stream, or None if unavailable."""
        return self._latest_prices.get(symbol.lower()) or self._latest_prices.get(symbol.upper())

    def is_healthy(self) -> bool:
        """Check if WS task is running."""
        return self._ws_task is not None and not self._ws_task.done()

    async def _rebuild_multiplex(self) -> None:
        """Stop existing WS task and start a new one with current subscriptions."""
        # Cancel existing task
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if not self._subscriptions:
            logger.info("KlineWsManager: no symbols, WS idle")
            return

        # Start new supervisor task
        self._ws_task = asyncio.create_task(
            self._supervisor_loop(), name="kline-ws-supervisor"
        )

    async def _supervisor_loop(self) -> None:
        """Outer supervisor that restarts WS on fatal failures."""
        backoff = _INITIAL_BACKOFF

        while self._running and self._subscriptions:
            try:
                await self._run_backfill()
                backoff = _INITIAL_BACKOFF  # reset after successful reconnect
                await self._run_multiplex()
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                logger.error("KlineWsManager: WS fatal error: %s, retrying in %ds", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

                # Recreate client and BSM
                try:
                    if self._async_client:
                        await self._async_client.close_connection()
                    from binance import AsyncClient
                    self._async_client = await AsyncClient.create()
                    self._bsm = None
                    logger.info("KlineWsManager: recreated AsyncClient after failure")
                except Exception as re_err:
                    logger.error("KlineWsManager: failed to recreate client: %s", re_err)
            else:
                # Normal exit (e.g., no more subscriptions)
                return

    async def _run_backfill(self) -> None:
        """Fetch last 60 1m candles for all subscribed symbols via REST."""
        if not self._async_client:
            return

        symbols = list(self._subscriptions.keys())
        for symbol in symbols:
            try:
                klines = await self._async_client.get_klines(
                    symbol=symbol.upper(), interval="1m", limit=60
                )
                candles = []
                for k in klines:
                    # Binance kline format: [open_time, open, high, low, close, volume, close_time, quote_volume, trades, ...]
                    candles.append({
                        "symbol": symbol.upper(),
                        "ts_ms": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "quote_volume": float(k[7]),
                        "trade_count": int(k[8]),
                    })
                if candles:
                    async with TradingSessionLocal() as session:
                        inserted = await store_candles_batch_1m(candles, session=session)
                        await session.commit()
                    # Update latest price from most recent candle
                    self._latest_prices[symbol] = candles[-1]["close"]
                    logger.info(
                        "KlineWsManager: backfilled %d/%d candles for %s",
                        inserted, len(candles), symbol.upper(),
                    )
            except Exception as e:
                logger.warning("KlineWsManager: backfill failed for %s: %s", symbol, e)

    async def _run_multiplex(self) -> None:
        """Run the multiplex WebSocket stream for all subscribed symbols."""
        if not self._async_client:
            from binance import AsyncClient
            self._async_client = await AsyncClient.create()

        from binance import BinanceSocketManager
        self._bsm = BinanceSocketManager(self._async_client)

        symbols = list(self._subscriptions.keys())
        if not symbols:
            return

        # Build stream names: <symbol>@kline_1m
        streams = [f"{s}@kline_1m" for s in symbols]

        logger.info(
            "KlineWsManager: starting multiplex for %d symbols: %s",
            len(symbols), [s.upper() for s in symbols],
        )

        async with self._bsm.multiplex_socket(streams) as stream:
            while self._running:
                msg = await stream.recv()
                if msg is None:
                    continue

                # Multiplex messages have 'stream' and 'data' keys
                data = msg.get("data", msg)
                if not isinstance(data, dict):
                    continue

                event_type = data.get("e")
                if event_type != "kline":
                    continue

                k = data.get("k", {})
                symbol_upper = k.get("s", "").upper()
                symbol_lower = symbol_upper.lower()

                # Update latest price on every event
                try:
                    close_price = float(k.get("c", 0))
                    if close_price > 0:
                        self._latest_prices[symbol_lower] = close_price
                        self._latest_prices[symbol_upper] = close_price
                except (ValueError, TypeError):
                    pass

                # Store only closed candles (k.x == true)
                is_closed = k.get("x", False)
                if not is_closed:
                    continue

                try:
                    ts_ms = int(k["t"])
                    open_ = float(k["o"])
                    high = float(k["h"])
                    low = float(k["l"])
                    close = float(k["c"])
                    volume = float(k["v"])
                    quote_volume = float(k["q"])
                    trade_count = int(k["n"])

                    async with TradingSessionLocal() as session:
                        await store_closed_candle_1m(
                            symbol=symbol_upper,
                            ts_ms=ts_ms,
                            open_=open_,
                            high=high,
                            low=low,
                            close=close,
                            volume=volume,
                            quote_volume=quote_volume,
                            trade_count=trade_count,
                            session=session,
                        )
                        await session.commit()
                    logger.debug("KlineWsManager: stored 1m candle %s @ %d", symbol_upper, ts_ms)
                except Exception as e:
                    logger.error(
                        "KlineWsManager: failed to store candle for %s: %s", symbol_upper, e
                    )
