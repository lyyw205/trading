from __future__ import annotations

import asyncio
import logging
import math
import time

import binance.client

from app.exchange.base_client import ExchangeClient, SymbolFilters

logger = logging.getLogger(__name__)


class BinanceClient(ExchangeClient):
    """Binance REST client that wraps python-binance's sync Client with asyncio.to_thread()."""

    def __init__(self, api_key: str, api_secret: str, symbol: str) -> None:
        self.symbol = symbol
        self.client = binance.client.Client(api_key, api_secret)
        self._filters_cache: dict[str, SymbolFilters] = {}
        self._balance_cache: dict[str, dict] = {}
        self._balance_cache_ts: float = 0.0
        self._sync_time_offset()

    # ------------------------------------------------------------------
    # Time sync
    # ------------------------------------------------------------------

    def _sync_time_offset(self) -> None:
        """Sync local clock with Binance server to avoid timestamp errors (WSL2 drift)."""
        try:
            server_time = self.client.get_server_time()
            self.client.timestamp_offset = server_time["serverTime"] - int(time.time() * 1000)
        except Exception:
            logger.warning("Binance time sync failed, using offset=0 (may cause signature errors)")
            self.client.timestamp_offset = 0

    # ------------------------------------------------------------------
    # Private sync helpers
    # ------------------------------------------------------------------

    def _sync_get_price(self, symbol: str) -> float:
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def _sync_get_symbol_filters(self, symbol: str) -> SymbolFilters:
        if symbol in self._filters_cache:
            return self._filters_cache[symbol]

        info = self.client.get_symbol_info(symbol)
        step_size = 0.0
        tick_size = 0.0
        min_notional = 0.0

        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
            elif f["filterType"] == "PRICE_FILTER":
                tick_size = float(f["tickSize"])
            elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                min_notional = float(f.get("minNotional", f.get("notional", 0.0)))

        filters = SymbolFilters(
            step_size=step_size,
            tick_size=tick_size,
            min_notional=min_notional,
        )
        self._filters_cache[symbol] = filters
        return filters

    @staticmethod
    def _floor_to_step(value: float, step: float) -> float:
        """Truncate value to nearest step multiple, avoiding float precision issues."""
        if step <= 0:
            return value
        precision = max(0, -int(math.floor(math.log10(step))))
        adjusted = math.floor(round(value / step, 8)) * step
        return round(adjusted, precision)

    def _sync_adjust_qty(self, qty: float, symbol: str) -> float:
        filters = self._sync_get_symbol_filters(symbol)
        return self._floor_to_step(qty, filters.step_size)

    def _sync_adjust_price(self, price: float, symbol: str) -> float:
        filters = self._sync_get_symbol_filters(symbol)
        return self._floor_to_step(price, filters.tick_size)

    def _sync_get_open_orders(self, symbol: str) -> list[dict]:
        return self.client.get_open_orders(symbol=symbol)

    def _sync_get_order(self, order_id: int, symbol: str) -> dict:
        return self.client.get_order(symbol=symbol, orderId=order_id)

    def _sync_cancel_order(self, order_id: int, symbol: str) -> dict:
        return self.client.cancel_order(symbol=symbol, orderId=order_id)

    def _sync_get_my_trades(
        self, symbol: str, limit: int = 1000, order_id: int | None = None
    ) -> list[dict]:
        kwargs: dict = {"symbol": symbol, "limit": limit}
        if order_id is not None:
            kwargs["orderId"] = order_id
        return self.client.get_my_trades(**kwargs)

    def _sync_place_limit_sell(
        self,
        qty_base: float,
        price: float,
        symbol: str,
        client_oid: str | None = None,
    ) -> dict:
        qty = self._sync_adjust_qty(qty_base, symbol)
        px = self._sync_adjust_price(price, symbol)
        kwargs: dict = {
            "symbol": symbol,
            "quantity": f"{qty:.8f}",
            "price": f"{px:.8f}",
            "timeInForce": "GTC",
        }
        if client_oid:
            kwargs["newClientOrderId"] = client_oid
        return self.client.order_limit_sell(**kwargs)

    def _sync_place_limit_buy_by_quote(
        self,
        quote_usdt: float,
        price: float,
        symbol: str,
        client_oid: str | None = None,
    ) -> dict:
        px = self._sync_adjust_price(price, symbol)
        qty = quote_usdt / px
        qty = self._sync_adjust_qty(qty, symbol)
        kwargs: dict = {
            "symbol": symbol,
            "quantity": f"{qty:.8f}",
            "price": f"{px:.8f}",
            "timeInForce": "GTC",
        }
        if client_oid:
            kwargs["newClientOrderId"] = client_oid
        return self.client.order_limit_buy(**kwargs)

    def _sync_get_balance(self, asset: str) -> dict:
        now = time.time()
        if now - self._balance_cache_ts > 5.0:
            account = self.client.get_account()
            self._balance_cache = {
                bal["asset"]: bal for bal in account["balances"]
            }
            self._balance_cache_ts = now
        bal = self._balance_cache.get(asset)
        if bal:
            free = float(bal["free"])
            locked = float(bal["locked"])
            return {"free": free, "locked": locked, "total": free + locked}
        return {"free": 0.0, "locked": 0.0, "total": 0.0}

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def get_price(self, symbol: str) -> float:
        return await asyncio.to_thread(self._sync_get_price, symbol)

    async def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        if symbol in self._filters_cache:
            return self._filters_cache[symbol]
        return await asyncio.to_thread(self._sync_get_symbol_filters, symbol)

    async def adjust_qty(self, qty: float, symbol: str) -> float:
        filters = await self.get_symbol_filters(symbol)
        return self._floor_to_step(qty, filters.step_size)

    async def adjust_price(self, price: float, symbol: str) -> float:
        filters = await self.get_symbol_filters(symbol)
        return self._floor_to_step(price, filters.tick_size)

    async def get_open_orders(self, symbol: str) -> list[dict]:
        return await asyncio.to_thread(self._sync_get_open_orders, symbol)

    async def get_order(self, order_id: int, symbol: str) -> dict:
        return await asyncio.to_thread(self._sync_get_order, order_id, symbol)

    async def cancel_order(self, order_id: int, symbol: str) -> dict:
        return await asyncio.to_thread(self._sync_cancel_order, order_id, symbol)

    async def get_my_trades(
        self, symbol: str, limit: int = 1000, order_id: int | None = None
    ) -> list[dict]:
        return await asyncio.to_thread(
            self._sync_get_my_trades, symbol, limit, order_id
        )

    async def place_limit_sell(
        self,
        qty_base: float,
        price: float,
        symbol: str,
        client_oid: str | None = None,
    ) -> dict:
        return await asyncio.to_thread(
            self._sync_place_limit_sell, qty_base, price, symbol, client_oid
        )

    async def place_limit_buy_by_quote(
        self,
        quote_usdt: float,
        price: float,
        symbol: str,
        client_oid: str | None = None,
    ) -> dict:
        return await asyncio.to_thread(
            self._sync_place_limit_buy_by_quote, quote_usdt, price, symbol, client_oid
        )

    async def get_balance(self, asset: str) -> dict:
        return await asyncio.to_thread(self._sync_get_balance, asset)

    async def get_free_balance(self, asset: str) -> float:
        bal = await self.get_balance(asset)
        return float(bal.get("free", 0.0))
