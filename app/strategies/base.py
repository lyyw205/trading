from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.strategies.constants import PENDING_KEYS

if TYPE_CHECKING:
    from app.db.lot_repo import LotRepository
    from app.db.order_repo import OrderRepository
    from app.db.position_repo import PositionRepository
    from app.db.price_repo import PriceRepository
    from app.exchange.base_client import ExchangeClient
    from app.models.lot import Lot
    from app.services.account_state_manager import AccountStateManager
    from app.strategies.state_store import StrategyStateStore

logger = logging.getLogger(__name__)


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
    free_balance: float = 0.0
    open_lots: list | None = None


@dataclass
class RepositoryBundle:
    """전략에 필요한 리포지토리 묶음"""

    lot: LotRepository
    order: OrderRepository
    position: PositionRepository
    price: PriceRepository


class _StrategyTimingMixin:
    """Shared timing helpers for buy/sell logic classes."""

    def __init__(self):
        self._last_order_ts: float = 0.0
        self._sim_time: float | None = None  # 백테스트용 시뮬레이션 시각

    def _now(self) -> float:
        return self._sim_time if self._sim_time is not None else time.time()

    def _cooldown_ok(self, cooldown_sec: float) -> bool:
        return (self._now() - self._last_order_ts) >= cooldown_sec

    def _touch_order(self) -> None:
        self._last_order_ts = self._now()


class BaseBuyLogic(_StrategyTimingMixin, ABC):
    """매수 전용 플러그인 기본 클래스.

    pending buy 주문의 체결 감시·만료 취소·키 정리는 Base의 `pre_tick`에서
    일괄 처리한다 (PAUSED에서도 돈다). 전략은 체결 시 수행할 사후 처리를
    `_handle_filled_buy`에 구현하고, 필요하면 `_should_cancel_pending_early`를
    오버라이드해 조기 취소 조건을 추가한다.
    """

    name: str = ""
    display_name: str = ""
    description: str = ""
    version: str = "1.0.0"
    default_params: dict[str, Any] = {}
    tunable_params: dict[str, dict[str, Any]] = {}

    # 매수 pending 주문 타임아웃 (ms). 전략마다 오버라이드 가능.
    _pending_timeout_ms: int = 5 * 60 * 1000

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "default_params" in cls.__dict__:
            cls.default_params = deepcopy(cls.default_params)
        if "tunable_params" in cls.__dict__:
            cls.tunable_params = deepcopy(cls.tunable_params)

    def __init__(self):
        super().__init__()

    async def pre_tick(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        combo_id: UUID,
    ) -> None:
        """매도 실행 이전·buy 일시중단 여부 판정 이전에 항상 호출.

        기본 구현은 pending buy 체결/취소/타임아웃을 처리한다. 전략이
        추가 사전 작업(recenter 등)을 하려면 `await super().pre_tick(...)`을
        먼저 호출한 뒤 본인의 로직을 붙인다.
        """
        await self._process_pending_buy(ctx, state, exchange, account_state, repos, combo_id)

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

    # ------------------------------------------------------------------
    # pending buy template (공통)
    # ------------------------------------------------------------------

    async def _process_pending_buy(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        combo_id: UUID,
    ) -> bool:
        """strategy_state의 pending_order_id를 보고 체결/취소/타임아웃을 반영한다.

        Returns True if a pending order existed this tick (regardless of terminal state),
        False if there was nothing to process. The tick path uses state re-read to decide
        whether to skip new-order placement, so the return value is informational.
        """
        pending_order_id = await state.get("pending_order_id")
        if not pending_order_id or str(pending_order_id).strip() == "":
            return False

        order_id = int(pending_order_id)
        pending_time_ms = await state.get_int("pending_time_ms", 0)
        pending_bucket = await state.get_float("pending_bucket_usdt", 0.0)
        pending_kind = await state.get("pending_kind", "LOT")
        pending_trigger = await state.get_float("pending_trigger_price", 0.0)

        try:
            order_data = await exchange.get_order(order_id, ctx.symbol)
        except Exception as exc:
            logger.error("%s: failed to fetch pending order %s: %s", self.name, order_id, exc)
            return True

        await repos.order.upsert_order(ctx.account_id, order_data)
        status = str(order_data.get("status", "")).upper()

        if status == "FILLED":
            logger.info("%s: pending buy order %s FILLED", self.name, order_id)
            await self._handle_filled_buy(
                ctx,
                state,
                order_data,
                account_state,
                repos,
                combo_id,
                kind=pending_kind,
                core_bucket_locked=pending_bucket,
            )
            await state.clear_keys(*PENDING_KEYS)
            return True

        if status in ("CANCELED", "REJECTED", "EXPIRED"):
            logger.info("%s: pending buy order %s %s", self.name, order_id, status)
            await state.clear_keys(*PENDING_KEYS)
            return True

        now_ms = int(self._now() * 1000)
        if pending_time_ms > 0 and (now_ms - pending_time_ms) > self._pending_timeout_ms:
            logger.warning("%s: pending buy order %s timed out, cancelling", self.name, order_id)
            try:
                cancel_resp = await exchange.cancel_order(order_id, ctx.symbol)
                await repos.order.upsert_order(ctx.account_id, cancel_resp)
            except Exception as exc:
                logger.error("%s: cancel timed-out order %s failed: %s", self.name, order_id, exc)
            await state.clear_keys(*PENDING_KEYS)
            return True

        if status == "NEW" and await self._should_cancel_pending_early(
            ctx, state, order_data, pending_kind=pending_kind, pending_trigger=pending_trigger
        ):
            logger.info("%s: cancelling pending buy order %s (early)", self.name, order_id)
            try:
                cancel_resp = await exchange.cancel_order(order_id, ctx.symbol)
                await repos.order.upsert_order(ctx.account_id, cancel_resp)
            except Exception as exc:
                logger.error("%s: early-cancel order %s failed: %s", self.name, order_id, exc)
            await state.clear_keys(*PENDING_KEYS)
            return True

        return True

    async def _handle_filled_buy(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        order_data: dict,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        combo_id: UUID,
        *,
        kind: str = "LOT",
        core_bucket_locked: float = 0.0,
    ) -> None:
        """pending buy 체결 시 호출되는 전략별 사후 처리 훅.

        일반적으로 `lots.insert_lot`과 `base_price` 등 전략 고유 상태 갱신을 수행한다.
        pending buy를 발행하는 전략은 반드시 오버라이드해야 한다.
        """
        raise NotImplementedError(
            f"{type(self).__name__} issues pending buy orders but does not implement _handle_filled_buy"
        )

    async def _should_cancel_pending_early(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        order_data: dict,
        *,
        pending_kind: str,
        pending_trigger: float,
    ) -> bool:
        """NEW 상태 pending buy를 타임아웃 전에 조기 취소할지 판단 (기본 False)."""
        return False


class BaseSellLogic(_StrategyTimingMixin, ABC):
    """매도 전용 플러그인 기본 클래스."""

    name: str = ""
    display_name: str = ""
    description: str = ""
    version: str = "1.0.0"
    default_params: dict[str, Any] = {}
    tunable_params: dict[str, dict[str, Any]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "default_params" in cls.__dict__:
            cls.default_params = deepcopy(cls.default_params)
        if "tunable_params" in cls.__dict__:
            cls.tunable_params = deepcopy(cls.tunable_params)

    def __init__(self):
        super().__init__()

    @abstractmethod
    async def tick(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        open_lots: list[Lot],
    ) -> None:
        """매도 로직 1 사이클. open_lots는 이 조합의 미결 로트들."""
        ...

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return {**self.default_params, **params}
