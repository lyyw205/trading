"""매수 금액 계산 모듈 — sizing mode별 매수 금액 결정.

3가지 모드:
- fixed: 고정 USDT 금액
- pct_balance: 현재 잔고의 N%
- scaled_plan: 회차별 점진적 증가 (buy-plan-calculator 로직)
"""

from __future__ import annotations

from enum import Enum


class SizingMode(str, Enum):
    FIXED = "fixed"
    PCT_BALANCE = "pct_balance"
    SCALED_PLAN = "scaled_plan"


def resolve_buy_usdt(
    params: dict,
    free_balance: float,
    sizing_round: int = 1,
    plan_5th_amount: float = 0.0,
) -> float:
    """sizing mode에 따라 매수 금액 결정. 순수 동기 함수.

    Args:
        params: combo의 buy_params (sizing_mode, buy_usdt, buy_balance_pct 등)
        free_balance: 현재 사용 가능한 USDT 잔고
        sizing_round: scaled_plan의 현재 회차 (1-based)
        plan_5th_amount: scaled_plan의 5회차 금액 A (6회차 이후 사용)

    Returns:
        매수할 USDT 금액
    """
    mode = params.get("sizing_mode", "fixed")
    max_cap = params.get("max_buy_usdt", 500.0)

    if mode == SizingMode.PCT_BALANCE:
        pct = params.get("buy_balance_pct", 10.0)
        amount = free_balance * (pct / 100.0)
        return min(amount, max_cap)

    if mode == SizingMode.SCALED_PLAN:
        x_pct = params.get("plan_x_pct", 0.5)
        if plan_5th_amount > 0 and sizing_round > 5:
            amount = plan_5th_amount
        else:
            amount = calc_scaled_plan_amount(free_balance, sizing_round, x_pct)
        return min(amount, max_cap)

    # fixed (default)
    return params.get("buy_usdt", 100.0)


def calc_scaled_plan_amount(
    balance: float,
    round_num: int,
    x_pct: float,
) -> float:
    """buy-plan-calculator calcBuyPlan() Python 포팅.

    round 1~5: balance * (round * x_pct / 100)
    round 6+:  balance * (5 * x_pct / 100) fallback

    Args:
        balance: 현재 USDT 잔고
        round_num: 현재 회차 (1-based)
        x_pct: 기본 비율 (%)
    """
    if round_num <= 0:
        round_num = 1
    x = x_pct / 100.0
    k = min(round_num, 5)
    return balance * (k * x)
