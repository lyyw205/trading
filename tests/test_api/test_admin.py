"""Integration tests for admin API endpoints."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.main import app


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.get_account_health = MagicMock(return_value={})
    engine.active_account_count = 0
    engine.get_ws_status = MagicMock(return_value={"healthy": True, "subscriptions": 0})
    return engine


@pytest_asyncio.fixture
async def admin_client_with_engine(admin_client, mock_engine):
    """admin_client with trading_engine injected into app state."""
    app.state.trading_engine = mock_engine
    yield admin_client


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_accounts_requires_auth(app_client):
    """GET /api/admin/accounts without a session cookie must be rejected."""
    response = await app_client.get("/api/admin/accounts")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# /api/admin/accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_accounts_returns_list(admin_client_with_engine):
    """GET /api/admin/accounts with admin auth must return 200 and a list."""
    response = await admin_client_with_engine.get("/api/admin/accounts")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# /api/admin/overview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_overview_returns_stats(admin_client_with_engine):
    """GET /api/admin/overview must return 200 with expected top-level keys."""
    response = await admin_client_with_engine.get("/api/admin/overview")
    assert response.status_code == 200
    data = response.json()
    assert "total_users" in data
    assert "total_accounts" in data
    assert "active_traders" in data
    assert "account_health" in data


# ---------------------------------------------------------------------------
# /api/admin/trades  (pagination)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_trades_pagination(admin_client_with_engine):
    """GET /api/admin/trades with limit/offset params must return 200 with pagination keys."""
    response = await admin_client_with_engine.get(
        "/api/admin/trades", params={"limit": 10, "offset": 0}
    )
    assert response.status_code == 200
    data = response.json()
    assert "trades" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data
    assert data["limit"] == 10
    assert data["offset"] == 0


# ---------------------------------------------------------------------------
# /api/admin/lots  (status filter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_lots_filtering(admin_client_with_engine):
    """GET /api/admin/lots with status param must return 200 with pagination keys."""
    response = await admin_client_with_engine.get(
        "/api/admin/lots", params={"status": "OPEN"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "lots" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data
