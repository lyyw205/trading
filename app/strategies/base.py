from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.strategies.state_store import StrategyStateStore
    from app.exchange.base_client import ExchangeClient
    from app.services.account_state_manager import AccountStateManager
    from app.db.lot_repo import LotRepository
    from app.db.order_repo import OrderRepository
    from app.db.position_repo import PositionRepository
    from app.db.price_repo import PriceRepository


@dataclass
class StrategyContext:
    """전략 실행에 필요한 불변 컨텍스트 (매 tick마다 새로 생성)"""
    account_id: UUID
    symbol: str
    base_asset: str
    quote_asset: str
    current_price: float
    params: Dict[str, Any]
    client_order_prefix: str


@dataclass
class RepositoryBundle:
    """전략에 필요한 리포지토리 묶음"""
    lot: "LotRepository"
    order: "OrderRepository"
    position: "PositionRepository"
    price: "PriceRepository"


class BaseStrategy(ABC):
    """
    전략 플러그인 베이스 클래스. tick() 패턴.
    """
    name: str = ""
    display_name: str = ""
    description: str = ""
    version: str = "1.0.0"
    default_params: Dict[str, Any] = {}
    tunable_params: Dict[str, Dict[str, Any]] = {}

    def __init__(self):
        self._last_order_ts: float = 0.0

    @abstractmethod
    async def tick(
        self,
        ctx: StrategyContext,
        state: "StrategyStateStore",
        exchange: "ExchangeClient",
        account_state: "AccountStateManager",
        repos: RepositoryBundle,
    ) -> None:
        """매매 사이클 1회 실행."""
        ...

    @abstractmethod
    async def on_fill(
        self,
        ctx: StrategyContext,
        state: "StrategyStateStore",
        fill_data: dict,
        account_state: "AccountStateManager",
        repos: RepositoryBundle,
    ) -> None:
        """주문 체결 후처리."""
        ...

    def validate_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """파라미터 유효성 검증 + 기본값 병합"""
        return {**self.default_params, **params}
