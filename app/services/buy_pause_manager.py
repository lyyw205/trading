"""
Buy Pause Manager — 잔고 부족 시 매수만 일시정지, 매도는 계속.

상태 전이:
  ACTIVE → (잔고 부족 1회) → THROTTLED → (3회 연속) → PAUSED
  PAUSED/THROTTLED → (잔고 회복) → ACTIVE
  수동 resume → ACTIVE (항상)
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import BuyPauseState, TradingAccount

logger = logging.getLogger(__name__)

# 잔고 충분 판정 기준 (USDT)
MIN_TRADE_USDT = 6.0

# THROTTLED: N루프 중 1회만 매수 시도
THROTTLE_EVERY_N = 5

# PAUSED + 포지션 없음 → deep sleep (초)
DEEP_SLEEP_SEC = 7200


class BuyPauseManager:
    """계정 단위 buy-pause 상태 관리. step()의 세션을 공유받음."""

    def __init__(self, account_id: UUID, session: AsyncSession):
        self._account_id = account_id
        self._session = session

    @staticmethod
    def should_attempt_buy(state: str, balance_ok: bool, throttle_cycle: int) -> tuple[bool, int]:
        """
        매수 시도 여부 판정.
        Returns (should_buy, updated_throttle_cycle).
        throttle_cycle은 AccountTrader에서 관리 (step() 간 유지).
        """
        if state == BuyPauseState.PAUSED:
            return False, throttle_cycle
        if state == BuyPauseState.THROTTLED:
            throttle_cycle += 1
            return throttle_cycle % THROTTLE_EVERY_N == 0, throttle_cycle
        # ACTIVE: 잔고가 있어야 시도
        return balance_ok, 0  # ACTIVE 복귀 시 카운터 리셋

    async def update_state(
        self,
        current_state: str,
        consecutive_low: int,
        balance_ok: bool,
        sell_occurred: bool,
    ) -> tuple[str, int]:
        """
        상태 전이 판정 + DB 업데이트.
        Returns (new_state, new_consecutive_low).
        """
        new_state = current_state
        new_consecutive = consecutive_low

        if balance_ok:
            # 잔고 회복 → 즉시 ACTIVE
            new_state = BuyPauseState.ACTIVE
            new_consecutive = 0
        elif sell_occurred and current_state == BuyPauseState.PAUSED:
            # 매도 발생했지만 잔고는 여전히 부족 → PAUSED 유지, 카운터만 증가
            new_consecutive = consecutive_low + 1
            logger.info(
                "[%s] Sell occurred but balance still low, staying PAUSED (count=%d)",
                self._account_id, new_consecutive,
            )
        else:
            # 잔고 부족
            new_consecutive = consecutive_low + 1
            if new_consecutive >= 3:
                new_state = BuyPauseState.PAUSED
            elif new_consecutive >= 1:
                new_state = BuyPauseState.THROTTLED

        # 상태가 바뀌었으면 DB 업데이트
        if new_state != current_state or new_consecutive != consecutive_low:
            values: dict = {
                "buy_pause_state": new_state,
                "consecutive_low_balance": new_consecutive,
            }

            if new_state == BuyPauseState.ACTIVE:
                values["buy_pause_reason"] = None
                values["buy_pause_since"] = None
                if current_state != BuyPauseState.ACTIVE:
                    logger.info("[%s] Buy pause cleared → ACTIVE", self._account_id)
            else:
                values["buy_pause_reason"] = "LOW_BALANCE"
                if current_state == BuyPauseState.ACTIVE:
                    values["buy_pause_since"] = datetime.utcnow()

                if new_state != current_state:
                    logger.warning(
                        "[%s] Buy pause → %s (consecutive=%d)",
                        self._account_id, new_state, new_consecutive,
                    )

            stmt = (
                update(TradingAccount)
                .where(TradingAccount.id == self._account_id)
                .values(**values)
            )
            await self._session.execute(stmt)

        return new_state, new_consecutive

    async def resume(self) -> None:
        """수동 재개 — ACTIVE 전환, 카운터 리셋."""
        stmt = (
            update(TradingAccount)
            .where(TradingAccount.id == self._account_id)
            .values(
                buy_pause_state=BuyPauseState.ACTIVE,
                buy_pause_reason=None,
                buy_pause_since=None,
                consecutive_low_balance=0,
            )
        )
        await self._session.execute(stmt)
        logger.info("[%s] Buy pause manually resumed → ACTIVE", self._account_id)

    @staticmethod
    def compute_interval(base_interval: int, state: str, has_positions: bool) -> float:
        """동적 루프 주기 계산."""
        if state == BuyPauseState.PAUSED and not has_positions:
            return float(DEEP_SLEEP_SEC)
        # ACTIVE, THROTTLED, PAUSED+positions → 정상 주기
        return float(base_interval)
