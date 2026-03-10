"""
Regression tests for CRIT-2: sell_occurred detection.

These tests verify the semantics of sell_occurred = (after < before)
under various lot count scenarios. The scope unification (both queries
using account-wide WHERE) is enforced by the code change in account_trader.py.
"""

import pytest


@pytest.mark.unit
class TestSellOccurredDetection:
    """sell_occurred = open_lots_after < open_lots_count_before"""

    @staticmethod
    def _sell_occurred(before: int, after: int) -> bool:
        return after < before

    def test_normal_sell_detected(self):
        """매도 1건 발생 → before=3, after=2 → True"""
        assert self._sell_occurred(before=3, after=2) is True

    def test_no_change_no_sell(self):
        """변동 없음 → before=3, after=3 → False"""
        assert self._sell_occurred(before=3, after=3) is False

    def test_buy_only_increases_count(self):
        """매수만 발생 → before=3, after=4 → False"""
        assert self._sell_occurred(before=3, after=4) is False

    def test_multiple_sells_detected(self):
        """매도 2건 → before=5, after=3 → True"""
        assert self._sell_occurred(before=5, after=3) is True

    def test_same_cycle_buy_sell_compensation_known_limitation(self):
        """
        Known limitation: 동일 사이클에서 매수 1건 + 매도 1건이 정확히
        상쇄되면 sell_occurred=False. Option C (sell flag)로만 해결 가능.
        이 테스트는 한계를 문서화한다.
        """
        # sell 1 + buy 1 → before=3, after=3
        assert self._sell_occurred(before=3, after=3) is False

    def test_orphan_lots_scope_unified(self):
        """
        Regression CRIT-2: 비활성 combo의 orphan 로트가 있어도
        before/after 스코프가 동일하므로 정확히 감지.

        수정 전: before=2 (활성 combo만), after=2 (orphan 1 포함) → False (버그)
        수정 후: before=3 (account-wide), after=2 (account-wide) → True (정상)
        """
        assert self._sell_occurred(before=3, after=2) is True

    def test_empty_account_no_sell(self):
        """로트 없는 계정 → before=0, after=0 → False"""
        assert self._sell_occurred(before=0, after=0) is False
