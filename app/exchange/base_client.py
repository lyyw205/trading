from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolFilters:
    step_size: float
    tick_size: float
    min_notional: float


class ExchangeClient(ABC):
    """거래소 클라이언트 비동기 인터페이스"""

    @abstractmethod
    async def get_price(self, symbol: str) -> float: ...

    @abstractmethod
    async def get_symbol_filters(self, symbol: str) -> SymbolFilters: ...

    @abstractmethod
    async def adjust_qty(self, qty: float, symbol: str) -> float: ...

    @abstractmethod
    async def adjust_price(self, price: float, symbol: str) -> float: ...

    @abstractmethod
    async def get_open_orders(self, symbol: str) -> list[dict]: ...

    @abstractmethod
    async def get_order(self, order_id: int, symbol: str) -> dict: ...

    @abstractmethod
    async def cancel_order(self, order_id: int, symbol: str) -> dict: ...

    @abstractmethod
    async def get_my_trades(self, symbol: str, limit: int = 1000,
                            order_id: int | None = None) -> list[dict]: ...

    @abstractmethod
    async def place_limit_sell(self, qty_base: float, price: float,
                               symbol: str, client_oid: str | None = None) -> dict: ...

    @abstractmethod
    async def place_limit_buy_by_quote(self, quote_usdt: float, price: float,
                                        symbol: str, client_oid: str | None = None) -> dict: ...

    @abstractmethod
    async def get_balance(self, asset: str) -> dict: ...

    @abstractmethod
    async def get_free_balance(self, asset: str) -> float: ...
