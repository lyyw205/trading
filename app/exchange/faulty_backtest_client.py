"""BacktestClient extension for failure mode testing."""
from __future__ import annotations

from app.exchange.backtest_client import BacktestClient


class FaultyBacktestClient(BacktestClient):
    """BacktestClient that can inject failures for testing error handling."""

    def __init__(
        self,
        symbol: str,
        initial_balance_usdt: float = 10000.0,
        initial_balance_btc: float = 0.0,
        fail_after: int = 0,
        fail_with: type[Exception] = ConnectionError,
        fail_message: str = "Simulated exchange failure",
        fail_on_methods: list[str] | None = None,
    ):
        super().__init__(symbol, initial_balance_usdt, initial_balance_btc)
        self._fail_after = fail_after
        self._fail_with = fail_with
        self._fail_message = fail_message
        self._fail_on_methods = fail_on_methods or [
            "get_price",
            "place_limit_buy_by_quote",
            "place_limit_sell",
        ]
        self._call_counts: dict[str, int] = {}

    def _check_failure(self, method_name: str) -> None:
        if method_name not in self._fail_on_methods:
            return
        count = self._call_counts.get(method_name, 0) + 1
        self._call_counts[method_name] = count
        # fail_after=0 means fail on every call; otherwise fail once count exceeds threshold
        if count > self._fail_after:
            raise self._fail_with(self._fail_message)

    async def get_price(self, symbol: str) -> float:
        self._check_failure("get_price")
        return await super().get_price(symbol)

    async def place_limit_buy_by_quote(
        self, quote_usdt, price, symbol, client_oid=None
    ):
        self._check_failure("place_limit_buy_by_quote")
        return await super().place_limit_buy_by_quote(
            quote_usdt, price, symbol, client_oid
        )

    async def place_limit_sell(self, qty_base, price, symbol, client_oid=None):
        self._check_failure("place_limit_sell")
        return await super().place_limit_sell(qty_base, price, symbol, client_oid)

    async def get_open_orders(self, symbol: str):
        self._check_failure("get_open_orders")
        return await super().get_open_orders(symbol)

    async def get_order(self, order_id: int, symbol: str):
        self._check_failure("get_order")
        return await super().get_order(order_id, symbol)

    async def get_balance(self, asset: str):
        self._check_failure("get_balance")
        return await super().get_balance(asset)

    async def get_free_balance(self, asset: str):
        self._check_failure("get_free_balance")
        return await super().get_free_balance(asset)

    def reset_failures(self) -> None:
        """Reset call counts to re-enable normal behavior."""
        self._call_counts.clear()

    def disable_failures(self) -> None:
        """Completely disable failure injection."""
        self._call_counts.clear()
        self._fail_on_methods = []
