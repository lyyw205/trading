"""
Integration tests for BuyPauseManager.update_state() with a real DB session.

These tests exercise the full state-machine including the SQLAlchemy UPDATE
statements.  Each test is rolled back via the SAVEPOINT pattern in conftest.py
so no state leaks between tests.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.account import BuyPauseState, TradingAccount
from app.models.user import UserProfile
from app.services.buy_pause_manager import BuyPauseManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_account(session, *, state: str = BuyPauseState.ACTIVE) -> TradingAccount:
    """Insert a minimal TradingAccount row and return it."""
    user = UserProfile(
        id=uuid.uuid4(),
        email=f"bp-test-{uuid.uuid4().hex[:8]}@example.com",
        role="user",
        password_hash="x",
    )
    session.add(user)
    await session.flush()

    account = TradingAccount(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="test-account",
        exchange="binance",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        api_key_encrypted="enc-key",
        api_secret_encrypted="enc-secret",
        buy_pause_state=state,
        consecutive_low_balance=0,
    )
    session.add(account)
    await session.flush()
    return account


async def _reload(session, account_id: uuid.UUID) -> TradingAccount:
    """Re-fetch account from DB to verify persisted values."""
    result = await session.execute(
        select(TradingAccount).where(TradingAccount.id == account_id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# State transition: ACTIVE -> THROTTLED -> PAUSED
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateStateTransitions:
    async def test_active_balance_ok_stays_active(self, db_session):
        account = await _create_account(db_session)
        mgr = BuyPauseManager(account.id, db_session)

        new_state, new_count = await mgr.update_state(
            current_state=BuyPauseState.ACTIVE,
            consecutive_low=0,
            balance_ok=True,
            sell_occurred=False,
        )

        assert new_state == BuyPauseState.ACTIVE
        assert new_count == 0

    async def test_active_balance_low_transitions_to_throttled(self, db_session):
        account = await _create_account(db_session)
        mgr = BuyPauseManager(account.id, db_session)

        new_state, new_count = await mgr.update_state(
            current_state=BuyPauseState.ACTIVE,
            consecutive_low=0,
            balance_ok=False,
            sell_occurred=False,
        )

        assert new_state == BuyPauseState.THROTTLED
        assert new_count == 1

    async def test_throttled_second_low_stays_throttled(self, db_session):
        account = await _create_account(db_session, state=BuyPauseState.THROTTLED)
        mgr = BuyPauseManager(account.id, db_session)

        new_state, new_count = await mgr.update_state(
            current_state=BuyPauseState.THROTTLED,
            consecutive_low=1,
            balance_ok=False,
            sell_occurred=False,
        )

        assert new_state == BuyPauseState.THROTTLED
        assert new_count == 2

    async def test_three_consecutive_low_transitions_to_paused(self, db_session):
        account = await _create_account(db_session, state=BuyPauseState.THROTTLED)
        mgr = BuyPauseManager(account.id, db_session)

        new_state, new_count = await mgr.update_state(
            current_state=BuyPauseState.THROTTLED,
            consecutive_low=2,
            balance_ok=False,
            sell_occurred=False,
        )

        assert new_state == BuyPauseState.PAUSED
        assert new_count == 3

    async def test_paused_balance_ok_transitions_to_active(self, db_session):
        account = await _create_account(db_session, state=BuyPauseState.PAUSED)
        mgr = BuyPauseManager(account.id, db_session)

        new_state, new_count = await mgr.update_state(
            current_state=BuyPauseState.PAUSED,
            consecutive_low=3,
            balance_ok=True,
            sell_occurred=False,
        )

        assert new_state == BuyPauseState.ACTIVE
        assert new_count == 0

    async def test_paused_sell_occurred_but_still_low_stays_paused(self, db_session):
        account = await _create_account(db_session, state=BuyPauseState.PAUSED)
        mgr = BuyPauseManager(account.id, db_session)

        new_state, new_count = await mgr.update_state(
            current_state=BuyPauseState.PAUSED,
            consecutive_low=3,
            balance_ok=False,
            sell_occurred=True,
        )

        assert new_state == BuyPauseState.PAUSED
        assert new_count == 4  # incremented but state unchanged


# ---------------------------------------------------------------------------
# DB persistence verification
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateStatePersistence:
    async def test_state_persisted_to_db_after_transition(self, db_session):
        account = await _create_account(db_session)
        mgr = BuyPauseManager(account.id, db_session)

        await mgr.update_state(
            current_state=BuyPauseState.ACTIVE,
            consecutive_low=0,
            balance_ok=False,
            sell_occurred=False,
        )
        await db_session.flush()

        reloaded = await _reload(db_session, account.id)
        assert reloaded.buy_pause_state == BuyPauseState.THROTTLED
        assert reloaded.consecutive_low_balance == 1

    async def test_reason_set_when_leaving_active(self, db_session):
        account = await _create_account(db_session)
        mgr = BuyPauseManager(account.id, db_session)

        await mgr.update_state(
            current_state=BuyPauseState.ACTIVE,
            consecutive_low=0,
            balance_ok=False,
            sell_occurred=False,
        )
        await db_session.flush()

        reloaded = await _reload(db_session, account.id)
        assert reloaded.buy_pause_reason == "LOW_BALANCE"
        assert reloaded.buy_pause_since is not None

    async def test_reason_cleared_when_returning_to_active(self, db_session):
        account = await _create_account(db_session, state=BuyPauseState.PAUSED)
        mgr = BuyPauseManager(account.id, db_session)

        await mgr.update_state(
            current_state=BuyPauseState.PAUSED,
            consecutive_low=3,
            balance_ok=True,
            sell_occurred=False,
        )
        await db_session.flush()

        reloaded = await _reload(db_session, account.id)
        assert reloaded.buy_pause_state == BuyPauseState.ACTIVE
        assert reloaded.buy_pause_reason is None
        assert reloaded.buy_pause_since is None

    async def test_consecutive_low_persisted_across_no_state_change(self, db_session):
        """Counter increments even when state stays THROTTLED."""
        account = await _create_account(db_session, state=BuyPauseState.THROTTLED)
        mgr = BuyPauseManager(account.id, db_session)

        await mgr.update_state(
            current_state=BuyPauseState.THROTTLED,
            consecutive_low=1,
            balance_ok=False,
            sell_occurred=False,
        )
        await db_session.flush()

        reloaded = await _reload(db_session, account.id)
        assert reloaded.consecutive_low_balance == 2

    async def test_resume_clears_all_pause_fields(self, db_session):
        account = await _create_account(db_session, state=BuyPauseState.PAUSED)
        mgr = BuyPauseManager(account.id, db_session)

        await mgr.resume()
        await db_session.flush()

        reloaded = await _reload(db_session, account.id)
        assert reloaded.buy_pause_state == BuyPauseState.ACTIVE
        assert reloaded.buy_pause_reason is None
        assert reloaded.buy_pause_since is None
        assert reloaded.consecutive_low_balance == 0

    async def test_no_db_write_when_nothing_changes(self, db_session):
        """
        When state and counter are unchanged (ACTIVE + balance_ok=True),
        no UPDATE is issued.  We verify by checking the account row stays
        identical.
        """
        account = await _create_account(db_session)
        mgr = BuyPauseManager(account.id, db_session)

        new_state, new_count = await mgr.update_state(
            current_state=BuyPauseState.ACTIVE,
            consecutive_low=0,
            balance_ok=True,
            sell_occurred=False,
        )

        assert new_state == BuyPauseState.ACTIVE
        assert new_count == 0
        # Row unchanged — flush should be a no-op
        await db_session.flush()
        reloaded = await _reload(db_session, account.id)
        assert reloaded.buy_pause_state == BuyPauseState.ACTIVE
        assert reloaded.consecutive_low_balance == 0
