"""Tests for StrategyStateStore — unit (mock session) + integration (db_session)."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.strategies.state_store import StrategyStateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(session=None, account_id=None, scope="test_scope"):
    if account_id is None:
        account_id = uuid.uuid4()
    if session is None:
        session = MagicMock()
    return StrategyStateStore(account_id=account_id, scope=scope, session=session)


def _mock_session_with_rows(rows: dict[str, str]) -> MagicMock:
    """Return a mock AsyncSession whose execute() yields key/value row objects."""
    session = MagicMock()

    # Build mock rows for get_all-style SELECT (key, value)
    mock_rows = []
    for k, v in rows.items():
        row = MagicMock()
        row.key = k
        row.value = v
        mock_rows.append(row)

    # execute() returns a result whose scalar_one_or_none() and __iter__ work
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter(mock_rows))
    result.scalar_one_or_none = MagicMock(return_value=None)

    session.execute = AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def account_id():
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Unit tests (mock session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preload_populates_cache(account_id):
    """After preload(), get() returns from cache without issuing another SQL query."""
    session = _mock_session_with_rows({"foo": "bar", "baz": "42"})
    store = StrategyStateStore(account_id=account_id, scope="s", session=session)

    assert store._cache is None
    await store.preload()

    assert store._cache == {"foo": "bar", "baz": "42"}
    # execute was called exactly once (the preload SELECT)
    call_count_after_preload = session.execute.call_count

    # get() should NOT call execute again
    val = await store.get("foo")
    assert val == "bar"
    assert session.execute.call_count == call_count_after_preload


@pytest.mark.asyncio
async def test_get_from_cache(account_id):
    """With _cache populated manually, get() returns the cached value."""
    session = MagicMock()
    session.execute = AsyncMock()
    store = _make_store(session=session, account_id=account_id)
    store._cache = {"key1": "value1"}

    result = await store.get("key1")

    assert result == "value1"
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_cache_miss_returns_default(account_id):
    """Cache exists but key not present → returns default without SQL."""
    session = MagicMock()
    session.execute = AsyncMock()
    store = _make_store(session=session, account_id=account_id)
    store._cache = {"other_key": "other_value"}

    result = await store.get("missing", default="sentinel")

    assert result == "sentinel"
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_set_updates_cache(account_id):
    """After set(), the value is written through to _cache."""
    session = MagicMock()
    session.execute = AsyncMock()
    store = _make_store(session=session, account_id=account_id)
    store._cache = {}

    await store.set("mykey", 123)

    assert store._cache["mykey"] == "123"


@pytest.mark.asyncio
async def test_set_many_updates_cache(account_id):
    """After set_many(), all supplied keys appear in _cache as strings."""
    session = MagicMock()
    session.execute = AsyncMock()
    store = _make_store(session=session, account_id=account_id)
    store._cache = {}

    await store.set_many({"a": 1, "b": 2.5, "c": "hello"})

    assert store._cache == {"a": "1", "b": "2.5", "c": "hello"}


@pytest.mark.asyncio
async def test_delete_evicts_from_cache(account_id):
    """After delete(), the key is removed from _cache."""
    session = MagicMock()
    session.execute = AsyncMock()
    store = _make_store(session=session, account_id=account_id)
    store._cache = {"to_delete": "val", "keep": "this"}

    await store.delete("to_delete")

    assert "to_delete" not in store._cache
    assert store._cache["keep"] == "this"


@pytest.mark.asyncio
async def test_clear_keys_evicts_from_cache(account_id):
    """After clear_keys(), all specified keys are removed from _cache."""
    session = MagicMock()
    session.execute = AsyncMock()
    store = _make_store(session=session, account_id=account_id)
    store._cache = {"a": "1", "b": "2", "c": "3"}

    await store.clear_keys("a", "b")

    assert "a" not in store._cache
    assert "b" not in store._cache
    assert store._cache["c"] == "3"


@pytest.mark.asyncio
async def test_with_scope_no_cache_inheritance(account_id):
    """with_scope() returns a new instance with _cache=None (not inherited)."""
    session = MagicMock()
    store = _make_store(session=session, account_id=account_id, scope="original")
    store._cache = {"populated": "yes"}

    new_store = store.with_scope("new_scope")

    assert new_store._cache is None
    assert new_store.scope == "new_scope"
    assert new_store.account_id == account_id
    assert new_store._session is session


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


async def _create_test_account(session):
    from app.models.account import TradingAccount
    from app.models.user import UserProfile

    user = UserProfile(
        id=uuid.uuid4(),
        email=f"test-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x",
        role="user",
    )
    session.add(user)
    await session.flush()

    acct = TradingAccount(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="test",
        exchange="binance",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        api_key_encrypted="enc-key",
        api_secret_encrypted="enc-secret",
    )
    session.add(acct)
    await session.flush()
    return acct


# ---------------------------------------------------------------------------
# Integration tests (db_session fixture)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_set_and_get_roundtrip(db_session):
    """set() persists to DB; get() (no cache) retrieves it."""
    acct = await _create_test_account(db_session)
    store = StrategyStateStore(account_id=acct.id, scope="roundtrip", session=db_session)

    await store.set("price", 99.5)
    # Clear cache so get() hits the DB
    store._cache = None
    result = await store.get("price")

    assert result == "99.5"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_preload_then_get(db_session):
    """After set + preload, get() reads from cache with correct values."""
    acct = await _create_test_account(db_session)
    store = StrategyStateStore(account_id=acct.id, scope="preload_get", session=db_session)

    await store.set("k1", "hello")
    await store.set("k2", "world")

    # Reset cache to force a fresh preload from DB
    store._cache = None
    await store.preload()

    assert await store.get("k1") == "hello"
    assert await store.get("k2") == "world"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_set_many_persistence(db_session):
    """set_many() persists all keys; each can be retrieved via get()."""
    acct = await _create_test_account(db_session)
    store = StrategyStateStore(account_id=acct.id, scope="set_many", session=db_session)

    await store.set_many({"x": "10", "y": "20", "z": "30"})
    store._cache = None

    assert await store.get("x") == "10"
    assert await store.get("y") == "20"
    assert await store.get("z") == "30"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_clear_keys_deletes(db_session):
    """clear_keys() removes specified keys; remaining keys are unaffected."""
    acct = await _create_test_account(db_session)
    store = StrategyStateStore(account_id=acct.id, scope="clear_keys", session=db_session)

    await store.set_many({"del1": "a", "del2": "b", "keep": "c"})
    await store.clear_keys("del1", "del2")
    store._cache = None

    assert await store.get("del1") is None
    assert await store.get("del2") is None
    assert await store.get("keep") == "c"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_float_and_int(db_session):
    """Numeric strings stored via set() are correctly parsed by get_float/get_int."""
    acct = await _create_test_account(db_session)
    store = StrategyStateStore(account_id=acct.id, scope="numeric", session=db_session)

    await store.set("price_f", "1234.56")
    await store.set("count_i", "7")
    store._cache = None

    assert await store.get_float("price_f") == pytest.approx(1234.56)
    assert await store.get_int("count_i") == 7
    assert await store.get_float("missing", 9.9) == pytest.approx(9.9)
    assert await store.get_int("missing", 42) == 42
