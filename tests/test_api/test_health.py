"""
Tests for the /health endpoint.

Uses @pytest.mark.unit because all DB calls are mocked — no real DB
connection is required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _mock_db_ok():
    """Patch _check_database to return a healthy response."""
    with patch(
        "app.api.health._check_database",
        new_callable=AsyncMock,
        return_value={"status": "ok", "latency_ms": 1.2},
    ) as m:
        yield m


@pytest.fixture
def _mock_db_error():
    """Patch _check_database to return an error response."""
    with patch(
        "app.api.health._check_database",
        new_callable=AsyncMock,
        return_value={"status": "error", "error": "connection refused"},
    ) as m:
        yield m


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _get_health(extra_state: dict | None = None) -> tuple[int, dict]:
    """Call /health via ASGI transport, optionally setting app.state fields."""
    from app.main import app

    if extra_state:
        for k, v in extra_state.items():
            setattr(app.state, k, v)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    return resp.status_code, resp.json()


# ---------------------------------------------------------------------------
# Response structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthResponseStructure:
    async def test_returns_200(self, _mock_db_ok):
        status_code, _ = await _get_health()
        assert status_code == 200

    async def test_contains_status_field(self, _mock_db_ok):
        _, body = await _get_health()
        assert "status" in body

    async def test_contains_version_field(self, _mock_db_ok):
        _, body = await _get_health()
        assert "version" in body
        assert isinstance(body["version"], str)

    async def test_contains_uptime_seconds(self, _mock_db_ok):
        _, body = await _get_health()
        assert "uptime_seconds" in body
        assert isinstance(body["uptime_seconds"], float | int)
        assert body["uptime_seconds"] >= 0

    async def test_contains_checks_key(self, _mock_db_ok):
        _, body = await _get_health()
        assert "checks" in body
        assert isinstance(body["checks"], dict)

    async def test_contains_alerts_list(self, _mock_db_ok):
        _, body = await _get_health()
        assert "alerts" in body
        assert isinstance(body["alerts"], list)


# ---------------------------------------------------------------------------
# DB latency included
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthDbLatency:
    async def test_db_check_present_in_checks(self, _mock_db_ok):
        _, body = await _get_health()
        assert "database" in body["checks"]

    async def test_db_latency_ms_present_when_healthy(self, _mock_db_ok):
        _, body = await _get_health()
        db = body["checks"]["database"]
        assert "latency_ms" in db
        assert isinstance(db["latency_ms"], float | int)

    async def test_db_status_ok_when_db_reachable(self, _mock_db_ok):
        _, body = await _get_health()
        assert body["checks"]["database"]["status"] == "ok"


# ---------------------------------------------------------------------------
# Overall status reflects DB health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthOverallStatus:
    async def test_healthy_when_db_ok_and_no_engine(self, _mock_db_ok):
        _, body = await _get_health()
        assert body["status"] == "healthy"

    async def test_unhealthy_when_db_error(self, _mock_db_error):
        _, body = await _get_health()
        assert body["status"] == "unhealthy"
        assert "database_unreachable" in body["alerts"]

    async def test_degraded_when_circuit_breaker_tripped(self, _mock_db_ok):
        mock_engine = MagicMock()
        mock_engine.get_account_health.return_value = {
            "acct-1": {"running": True, "circuit_breaker_tripped": True},
        }
        _, body = await _get_health(extra_state={"trading_engine": mock_engine})
        assert body["status"] == "degraded"
        assert "circuit_breaker_active" in body["alerts"]

    async def test_healthy_when_engine_has_no_tripped_breakers(self, _mock_db_ok):
        mock_engine = MagicMock()
        mock_engine.get_account_health.return_value = {
            "acct-1": {"running": True, "circuit_breaker_tripped": False},
        }
        _, body = await _get_health(extra_state={"trading_engine": mock_engine})
        assert body["status"] == "healthy"
        assert "circuit_breaker_active" not in body["alerts"]
