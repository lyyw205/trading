from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from app.db.lot_repo import LotRepository
    from app.db.order_repo import OrderRepository
    from app.db.position_repo import PositionRepository
    from app.db.price_repo import PriceRepository
    from app.exchange.base_client import ExchangeClient
    from app.services.account_state_manager import AccountStateManager
    from app.strategies.state_store import StrategyStateStore


@dataclass
class StrategyContext:
    """전략 실행에 필요한 불변 컨텍스트 (매 tick마다 새로 생성)"""
    account_id: UUID
    symbol: str
    base_asset: str
    quote_asset: str
    current_price: float
    params: dict[str, Any]
    client_order_prefix: str


@dataclass
class RepositoryBundle:
    """전략에 필요한 리포지토리 묶음"""
    lot: LotRepository
    order: OrderRepository
    position: PositionRepository
    price: PriceRepository


class BaseBuyLogic(ABC):
    """매수 전용 플러그인 기본 클래스."""
    name: str = ""
    display_name: str = ""
    description: str = ""
    version: str = "1.0.0"
    default_params: dict[str, Any] = {}
    tunable_params: dict[str, dict[str, Any]] = {}

    def __init__(self):
        self._last_order_ts: float = 0.0

    async def pre_tick(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        repos: RepositoryBundle,
        combo_id: UUID,
    ) -> None:
        """매도 실행 이전에 호출 (기본: no-op). recenter 등 base_price 순서 보호용."""
        pass

    @abstractmethod
    async def tick(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        combo_id: UUID,
    ) -> None:
        """매수 로직 1 사이클 (매도 실행 이후에 호출)."""
        ...

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return {**self.default_params, **params}

    def _cooldown_ok(self, cooldown_sec: float) -> bool:
        return (time.time() - self._last_order_ts) >= cooldown_sec

    def _touch_order(self) -> None:
        self._last_order_ts = time.time()


class BaseSellLogic(ABC):
    """매도 전용 플러그인 기본 클래스."""
    name: str = ""
    display_name: str = ""
    description: str = ""
    version: str = "1.0.0"
    default_params: dict[str, Any] = {}
    tunable_params: dict[str, dict[str, Any]] = {}

    def __init__(self):
        self._last_order_ts: float = 0.0

    @abstractmethod
    async def tick(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        open_lots: list,
    ) -> None:
        """매도 로직 1 사이클. open_lots는 이 조합의 미결 로트들."""
        ...

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return {**self.default_params, **params}

    def _cooldown_ok(self, cooldown_sec: float) -> bool:
        return (time.time() - self._last_order_ts) >= cooldown_sec

    def _touch_order(self) -> None:
        self._last_order_ts = time.time()
