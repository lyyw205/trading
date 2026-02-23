from __future__ import annotations
import math
import time
import uuid
from typing import Optional, List

from app.exchange.base_client import ExchangeClient, SymbolFilters


class BacktestClient(ExchangeClient):
    """
    ExchangeClient implementation for backtesting.
    Simulates exchange behaviour with historical price data.
    All state is in-memory; no real API calls are made.
    """

    def __init__(
        self,
        symbol: str,
        initial_balance_usdt: float = 10000.0,
        initial_balance_btc: float = 0.0,
    ):
        self.symbol = symbol
        self._current_price: float = 0.0
        self._balances: dict[str, dict] = {
            "USDT": {"free": initial_balance_usdt, "locked": 0.0},
            "BTC": {"free": initial_balance_btc, "locked": 0.0},
        }
        self._open_orders: list[dict] = []
        self._filled_orders: list[dict] = []
        self._trades: list[dict] = []
        self._order_id_counter = 1
        self._filters = SymbolFilters(
            step_size=0.00001, tick_size=0.01, min_notional=10.0
        )

    # ------------------------------------------------------------------
    # Price helpers
    # ------------------------------------------------------------------

    def set_price(self, price: float) -> None:
        """Set current simulated price and check limit order fills."""
        self._current_price = price
        self._check_order_fills()

    def _check_order_fills(self) -> None:
        """Check if any open orders should be filled at the current price."""
        remaining: list[dict] = []
        for order in self._open_orders:
            filled = False
            order_price = float(order["price"])
            if order["side"] == "BUY" and self._current_price <= order_price:
                filled = True
            elif order["side"] == "SELL" and self._current_price >= order_price:
                filled = True

            if filled:
                order["status"] = "FILLED"
                order["executedQty"] = order["origQty"]
                qty = float(order["origQty"])
                px = float(order["price"])
                asset = order["symbol"].replace("USDT", "")

                if order["side"] == "BUY":
                    cost = qty * px
                    self._balances["USDT"]["locked"] = max(
                        0.0, self._balances["USDT"]["locked"] - cost
                    )
                    if asset not in self._balances:
                        self._balances[asset] = {"free": 0.0, "locked": 0.0}
                    self._balances[asset]["free"] += qty
                else:  # SELL
                    if asset not in self._balances:
                        self._balances[asset] = {"free": 0.0, "locked": 0.0}
                    self._balances[asset]["locked"] = max(
                        0.0, self._balances[asset]["locked"] - qty
                    )
                    self._balances["USDT"]["free"] += qty * px

                self._filled_orders.append(order)
                self._trades.append(
                    {
                        "id": len(self._trades) + 1,
                        "orderId": order["orderId"],
                        "symbol": order["symbol"],
                        "side": order["side"],
                        "price": order["price"],
                        "qty": order["origQty"],
                        "quoteQty": str(qty * px),
                        "commission": "0",
                        "commissionAsset": "BNB",
                        "time": int(time.time() * 1000),
                        "isBuyer": order["side"] == "BUY",
                    }
                )
            else:
                remaining.append(order)

        self._open_orders = remaining

    def _next_order_id(self) -> int:
        oid = self._order_id_counter
        self._order_id_counter += 1
        return oid

    # ------------------------------------------------------------------
    # ExchangeClient interface
    # ------------------------------------------------------------------

    async def get_price(self, symbol: str) -> float:
        return self._current_price

    async def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        return self._filters

    async def adjust_qty(self, qty: float, symbol: str) -> float:
        step = self._filters.step_size
        if step <= 0:
            return qty
        precision = max(0, -int(math.floor(math.log10(step))))
        adjusted = math.floor(qty / step) * step
        return round(adjusted, precision)

    async def adjust_price(self, price: float, symbol: str) -> float:
        tick = self._filters.tick_size
        if tick <= 0:
            return price
        precision = max(0, -int(math.floor(math.log10(tick))))
        adjusted = math.floor(price / tick) * tick
        return round(adjusted, precision)

    async def get_open_orders(self, symbol: str) -> List[dict]:
        return [o for o in self._open_orders if o["symbol"] == symbol]

    async def get_order(self, order_id: int, symbol: str) -> dict:
        # Search open orders first, then filled
        for o in self._open_orders:
            if o["orderId"] == order_id and o["symbol"] == symbol:
                return dict(o)
        for o in self._filled_orders:
            if o["orderId"] == order_id and o["symbol"] == symbol:
                return dict(o)
        return {
            "orderId": order_id,
            "symbol": symbol,
            "status": "NOT_FOUND",
            "origQty": "0",
            "executedQty": "0",
            "price": "0",
            "side": "",
        }

    async def cancel_order(self, order_id: int, symbol: str) -> dict:
        remaining: list[dict] = []
        cancelled: dict | None = None
        for o in self._open_orders:
            if o["orderId"] == order_id and o["symbol"] == symbol:
                cancelled = o
                o["status"] = "CANCELED"
                # Return locked funds
                qty = float(o["origQty"])
                px = float(o["price"])
                asset = o["symbol"].replace("USDT", "")
                if o["side"] == "BUY":
                    cost = qty * px
                    self._balances["USDT"]["locked"] = max(
                        0.0, self._balances["USDT"]["locked"] - cost
                    )
                    self._balances["USDT"]["free"] += cost
                else:
                    if asset not in self._balances:
                        self._balances[asset] = {"free": 0.0, "locked": 0.0}
                    self._balances[asset]["locked"] = max(
                        0.0, self._balances[asset]["locked"] - qty
                    )
                    self._balances[asset]["free"] += qty
            else:
                remaining.append(o)

        self._open_orders = remaining
        if cancelled:
            return dict(cancelled)
        return {"orderId": order_id, "symbol": symbol, "status": "CANCELED"}

    async def get_my_trades(
        self, symbol: str, limit: int = 1000, order_id: Optional[int] = None
    ) -> List[dict]:
        trades = [t for t in self._trades if t["symbol"] == symbol]
        if order_id is not None:
            trades = [t for t in trades if t["orderId"] == order_id]
        return trades[-limit:]

    async def place_limit_buy_by_quote(
        self,
        quote_usdt: float,
        price: float,
        symbol: str,
        client_oid: Optional[str] = None,
    ) -> dict:
        if price <= 0:
            raise ValueError("price must be > 0")
        qty = await self.adjust_qty(quote_usdt / price, symbol)
        if qty <= 0:
            raise ValueError("Computed qty is zero after adjustment")

        cost = qty * price
        usdt_free = self._balances["USDT"]["free"]
        if usdt_free < cost:
            raise ValueError(
                f"Insufficient USDT balance: need {cost:.4f}, have {usdt_free:.4f}"
            )

        self._balances["USDT"]["free"] -= cost
        self._balances["USDT"]["locked"] += cost

        order_id = self._next_order_id()
        order = {
            "orderId": order_id,
            "clientOrderId": client_oid or str(uuid.uuid4()),
            "symbol": symbol,
            "side": "BUY",
            "type": "LIMIT",
            "status": "NEW",
            "price": str(price),
            "origQty": str(qty),
            "executedQty": "0",
            "cummulativeQuoteQty": "0",
            "timeInForce": "GTC",
            "transactTime": int(time.time() * 1000),
        }
        self._open_orders.append(order)
        self._check_order_fills()
        return dict(order)

    async def place_limit_sell(
        self,
        qty_base: float,
        price: float,
        symbol: str,
        client_oid: Optional[str] = None,
    ) -> dict:
        if price <= 0:
            raise ValueError("price must be > 0")
        qty = await self.adjust_qty(qty_base, symbol)
        if qty <= 0:
            raise ValueError("qty is zero after adjustment")

        asset = symbol.replace("USDT", "")
        if asset not in self._balances:
            self._balances[asset] = {"free": 0.0, "locked": 0.0}
        asset_free = self._balances[asset]["free"]
        if asset_free < qty:
            raise ValueError(
                f"Insufficient {asset} balance: need {qty:.8f}, have {asset_free:.8f}"
            )

        self._balances[asset]["free"] -= qty
        self._balances[asset]["locked"] += qty

        order_id = self._next_order_id()
        order = {
            "orderId": order_id,
            "clientOrderId": client_oid or str(uuid.uuid4()),
            "symbol": symbol,
            "side": "SELL",
            "type": "LIMIT",
            "status": "NEW",
            "price": str(price),
            "origQty": str(qty),
            "executedQty": "0",
            "cummulativeQuoteQty": "0",
            "timeInForce": "GTC",
            "transactTime": int(time.time() * 1000),
        }
        self._open_orders.append(order)
        self._check_order_fills()
        return dict(order)

    async def get_balance(self, asset: str) -> dict:
        bal = self._balances.get(asset, {"free": 0.0, "locked": 0.0})
        free = float(bal["free"])
        locked = float(bal["locked"])
        return {"free": free, "locked": locked, "total": free + locked}

    async def get_free_balance(self, asset: str) -> float:
        return self._balances.get(asset, {"free": 0.0, "locked": 0.0})["free"]
