"""app.strategies.sizing 단위 테스트."""

import pytest

from app.strategies.sizing import (
    calc_scaled_plan_amount,
    resolve_buy_usdt,
)


class TestResolveBuyUsdt:
    """resolve_buy_usdt() 테스트."""

    def test_fixed_default(self):
        params = {"sizing_mode": "fixed", "buy_usdt": 100.0}
        assert resolve_buy_usdt(params, free_balance=5000.0) == 100.0

    def test_fixed_no_mode_defaults(self):
        """sizing_mode 미설정 → fixed 기본값."""
        params = {"buy_usdt": 200.0}
        assert resolve_buy_usdt(params, free_balance=5000.0) == 200.0

    def test_fixed_ignores_max_cap(self):
        """fixed 모드는 max_buy_usdt 무시."""
        params = {"sizing_mode": "fixed", "buy_usdt": 1000.0, "max_buy_usdt": 500.0}
        assert resolve_buy_usdt(params, free_balance=5000.0) == 1000.0

    def test_pct_balance_basic(self):
        params = {"sizing_mode": "pct_balance", "buy_balance_pct": 10.0, "max_buy_usdt": 500.0}
        assert resolve_buy_usdt(params, free_balance=1000.0) == 100.0

    def test_pct_balance_capped(self):
        params = {"sizing_mode": "pct_balance", "buy_balance_pct": 50.0, "max_buy_usdt": 500.0}
        result = resolve_buy_usdt(params, free_balance=10000.0)
        assert result == 500.0  # 5000 capped to 500

    def test_pct_balance_zero_balance(self):
        params = {"sizing_mode": "pct_balance", "buy_balance_pct": 10.0, "max_buy_usdt": 500.0}
        assert resolve_buy_usdt(params, free_balance=0.0) == 0.0

    def test_scaled_plan_round_1(self):
        params = {"sizing_mode": "scaled_plan", "plan_x_pct": 0.5, "max_buy_usdt": 500.0}
        result = resolve_buy_usdt(params, free_balance=2000.0, sizing_round=1)
        assert result == pytest.approx(10.0)  # 2000 * 1 * 0.005

    def test_scaled_plan_round_3(self):
        params = {"sizing_mode": "scaled_plan", "plan_x_pct": 0.5, "max_buy_usdt": 500.0}
        result = resolve_buy_usdt(params, free_balance=2000.0, sizing_round=3)
        assert result == pytest.approx(30.0)  # 2000 * 3 * 0.005

    def test_scaled_plan_round_5(self):
        params = {"sizing_mode": "scaled_plan", "plan_x_pct": 0.5, "max_buy_usdt": 500.0}
        result = resolve_buy_usdt(params, free_balance=2000.0, sizing_round=5)
        assert result == pytest.approx(50.0)  # 2000 * 5 * 0.005

    def test_scaled_plan_round_6_with_5th_amount(self):
        """round 6+: plan_5th_amount(A) 사용."""
        params = {"sizing_mode": "scaled_plan", "plan_x_pct": 0.5, "max_buy_usdt": 500.0}
        result = resolve_buy_usdt(
            params, free_balance=2000.0, sizing_round=6, plan_5th_amount=45.0,
        )
        assert result == 45.0

    def test_scaled_plan_round_6_no_5th_fallback(self):
        """round 6+: plan_5th_amount=0이면 balance * 5 * x fallback."""
        params = {"sizing_mode": "scaled_plan", "plan_x_pct": 0.5, "max_buy_usdt": 500.0}
        result = resolve_buy_usdt(
            params, free_balance=2000.0, sizing_round=8, plan_5th_amount=0.0,
        )
        assert result == pytest.approx(50.0)  # fallback: 2000 * 5 * 0.005

    def test_scaled_plan_capped(self):
        params = {"sizing_mode": "scaled_plan", "plan_x_pct": 10.0, "max_buy_usdt": 100.0}
        result = resolve_buy_usdt(params, free_balance=5000.0, sizing_round=5)
        assert result == 100.0  # 5000 * 5 * 0.1 = 2500 → capped to 100

    def test_scaled_plan_round_0_correction(self):
        """round=0 → round=1로 보정."""
        params = {"sizing_mode": "scaled_plan", "plan_x_pct": 1.0, "max_buy_usdt": 500.0}
        result = resolve_buy_usdt(params, free_balance=1000.0, sizing_round=0)
        assert result == pytest.approx(10.0)  # 1000 * 1 * 0.01

    def test_scaled_plan_zero_balance(self):
        params = {"sizing_mode": "scaled_plan", "plan_x_pct": 0.5, "max_buy_usdt": 500.0}
        assert resolve_buy_usdt(params, free_balance=0.0, sizing_round=3) == 0.0


class TestCalcScaledPlanAmount:
    """calc_scaled_plan_amount() 테스트."""

    def test_round_1(self):
        assert calc_scaled_plan_amount(2000.0, 1, 0.5) == pytest.approx(10.0)

    def test_round_5(self):
        assert calc_scaled_plan_amount(2000.0, 5, 0.5) == pytest.approx(50.0)

    def test_round_6_clamped_to_5(self):
        """round 6 이상은 round 5와 동일 계산."""
        assert calc_scaled_plan_amount(2000.0, 6, 0.5) == pytest.approx(50.0)
        assert calc_scaled_plan_amount(2000.0, 10, 0.5) == pytest.approx(50.0)

    def test_round_0_corrected(self):
        """round 0 → round 1로 보정."""
        assert calc_scaled_plan_amount(1000.0, 0, 1.0) == pytest.approx(10.0)

    def test_round_negative_corrected(self):
        """음수 round → round 1로 보정."""
        assert calc_scaled_plan_amount(1000.0, -5, 1.0) == pytest.approx(10.0)

    def test_zero_balance(self):
        assert calc_scaled_plan_amount(0.0, 3, 0.5) == 0.0

    def test_large_x_pct(self):
        result = calc_scaled_plan_amount(1000.0, 5, 10.0)
        assert result == pytest.approx(500.0)  # 1000 * 5 * 0.1
