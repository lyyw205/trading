"""BuyPauseManager pure-logic unit tests — no DB, no async."""
import pytest

from app.models.account import BuyPauseState
from app.services.buy_pause_manager import (
    DEEP_SLEEP_SEC,
    MIN_TRADE_USDT,
    THROTTLE_EVERY_N,
    BuyPauseManager,
)

# ---------------------------------------------------------------------------
# BuyPauseManager.should_attempt_buy()
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestShouldAttemptBuy:
    """should_attempt_buy(state, balance_ok, throttle_cycle) -> (bool, int)."""

    # --- ACTIVE state ---

    def test_active_balance_ok_returns_true_cycle_zero(self):
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.ACTIVE, balance_ok=True, throttle_cycle=7
        )
        assert ok is True
        assert cycle == 0  # ACTIVE always resets the counter

    def test_active_balance_not_ok_returns_false_cycle_zero(self):
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.ACTIVE, balance_ok=False, throttle_cycle=3
        )
        assert ok is False
        assert cycle == 0  # counter is still reset on ACTIVE path

    def test_active_resets_throttle_cycle_regardless_of_input(self):
        for incoming_cycle in (0, 1, 4, 99):
            _, out_cycle = BuyPauseManager.should_attempt_buy(
                BuyPauseState.ACTIVE, balance_ok=True, throttle_cycle=incoming_cycle
            )
            assert out_cycle == 0, f"Expected 0 reset, got {out_cycle}"

    # --- PAUSED state ---

    def test_paused_balance_ok_returns_false_cycle_unchanged(self):
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.PAUSED, balance_ok=True, throttle_cycle=2
        )
        assert ok is False
        assert cycle == 2  # untouched

    def test_paused_balance_not_ok_returns_false_cycle_unchanged(self):
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.PAUSED, balance_ok=False, throttle_cycle=0
        )
        assert ok is False
        assert cycle == 0

    def test_paused_large_cycle_unchanged(self):
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.PAUSED, balance_ok=True, throttle_cycle=99
        )
        assert ok is False
        assert cycle == 99

    # --- THROTTLED state ---

    def test_throttled_cycle_0_increments_to_1_not_multiple_of_5(self):
        # cycle 0 -> 1; 1 % 5 != 0 -> False
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.THROTTLED, balance_ok=True, throttle_cycle=0
        )
        assert ok is False
        assert cycle == 1

    def test_throttled_cycle_1_increments_to_2(self):
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.THROTTLED, balance_ok=True, throttle_cycle=1
        )
        assert ok is False
        assert cycle == 2

    def test_throttled_cycle_2_increments_to_3(self):
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.THROTTLED, balance_ok=False, throttle_cycle=2
        )
        assert ok is False
        assert cycle == 3

    def test_throttled_cycle_3_increments_to_4(self):
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.THROTTLED, balance_ok=True, throttle_cycle=3
        )
        assert ok is False
        assert cycle == 4

    def test_throttled_cycle_4_increments_to_5_allows_buy(self):
        # cycle 4 -> 5; 5 % 5 == 0 -> True (every-Nth buy)
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.THROTTLED, balance_ok=True, throttle_cycle=4
        )
        assert ok is True
        assert cycle == 5

    def test_throttled_cycle_9_increments_to_10_allows_buy(self):
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.THROTTLED, balance_ok=False, throttle_cycle=9
        )
        # balance_ok is irrelevant in THROTTLED; only cycle matters
        assert ok is True
        assert cycle == 10

    def test_throttled_every_fifth_cycle_is_always_true(self):
        """Cycles whose (cycle+1) % THROTTLE_EVERY_N == 0 should allow buy."""
        for base in range(0, 25):
            ok, out_cycle = BuyPauseManager.should_attempt_buy(
                BuyPauseState.THROTTLED, balance_ok=True, throttle_cycle=base
            )
            expected_buy = (base + 1) % THROTTLE_EVERY_N == 0
            assert ok == expected_buy, (
                f"cycle {base} -> {out_cycle}: expected ok={expected_buy}, got {ok}"
            )

    def test_throttled_balance_ok_false_does_not_suppress_nth_cycle(self):
        """THROTTLED ignores balance_ok; the Nth cycle fires regardless."""
        ok, cycle = BuyPauseManager.should_attempt_buy(
            BuyPauseState.THROTTLED, balance_ok=False, throttle_cycle=4
        )
        assert ok is True
        assert cycle == 5

    # --- Constants sanity ---

    def test_constants_values(self):
        assert MIN_TRADE_USDT == 6.0
        assert THROTTLE_EVERY_N == 5
        assert DEEP_SLEEP_SEC == 7200


# ---------------------------------------------------------------------------
# BuyPauseManager.compute_interval()
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestComputeInterval:
    """compute_interval(base_interval, state, has_positions) -> float."""

    def test_active_returns_base_interval(self):
        result = BuyPauseManager.compute_interval(60, BuyPauseState.ACTIVE, has_positions=False)
        assert result == 60.0
        assert isinstance(result, float)

    def test_active_with_positions_returns_base_interval(self):
        result = BuyPauseManager.compute_interval(30, BuyPauseState.ACTIVE, has_positions=True)
        assert result == 30.0

    def test_throttled_returns_base_interval(self):
        result = BuyPauseManager.compute_interval(45, BuyPauseState.THROTTLED, has_positions=False)
        assert result == 45.0
        assert isinstance(result, float)

    def test_throttled_with_positions_returns_base_interval(self):
        result = BuyPauseManager.compute_interval(90, BuyPauseState.THROTTLED, has_positions=True)
        assert result == 90.0

    def test_paused_with_positions_returns_base_interval(self):
        result = BuyPauseManager.compute_interval(60, BuyPauseState.PAUSED, has_positions=True)
        assert result == 60.0

    def test_paused_no_positions_returns_deep_sleep(self):
        result = BuyPauseManager.compute_interval(60, BuyPauseState.PAUSED, has_positions=False)
        assert result == float(DEEP_SLEEP_SEC)
        assert result == 7200.0

    def test_paused_no_positions_deep_sleep_is_float(self):
        result = BuyPauseManager.compute_interval(10, BuyPauseState.PAUSED, has_positions=False)
        assert isinstance(result, float)
        assert result == 7200.0

    def test_return_type_is_always_float(self):
        for state in (BuyPauseState.ACTIVE, BuyPauseState.THROTTLED):
            result = BuyPauseManager.compute_interval(60, state, has_positions=False)
            assert isinstance(result, float), f"Expected float for state={state}"
