"""Unit tests for AlertService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.alert_service import AlertService, AlertSeverity


def _make_settings(token: str = "tok", chat_id: str = "cid", rate_limit: int = 3):
    settings = MagicMock()
    settings.telegram_bot_token = token
    settings.telegram_chat_id = chat_id
    settings.alert_rate_limit_per_hour = rate_limit
    return settings


def _mock_client(status_code: int = 200, text: str = "ok"):
    """Return a mock httpx.AsyncClient with preset response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text

    client = AsyncMock()
    client.post = AsyncMock(return_value=mock_response)
    return client


@pytest.mark.unit
def test_disabled_when_no_token():
    settings = _make_settings(token="", chat_id="cid")
    svc = AlertService(settings)
    assert not svc.is_enabled


@pytest.mark.unit
def test_disabled_when_no_chat_id():
    settings = _make_settings(token="tok", chat_id="")
    svc = AlertService(settings)
    assert not svc.is_enabled


@pytest.mark.unit
def test_enabled_with_both_token_and_chat_id():
    settings = _make_settings(token="tok", chat_id="cid")
    svc = AlertService(settings)
    assert svc.is_enabled


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_returns_false_when_disabled():
    settings = _make_settings(token="", chat_id="")
    svc = AlertService(settings)
    result = await svc.send("hello")
    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limiting_rejects_over_limit():
    """Send rate_limit messages successfully, then N+1 should be rejected."""
    settings = _make_settings(rate_limit=3)
    svc = AlertService(settings)
    svc._client = _mock_client(status_code=200)

    # First 3 succeed
    for _ in range(3):
        result = await svc.send("msg", AlertSeverity.INFO)
        assert result is True

    # 4th is rate-limited
    result = await svc.send("msg", AlertSeverity.INFO)
    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critical_bypasses_rate_limit():
    """CRITICAL severity must bypass the rate limit."""
    settings = _make_settings(rate_limit=2)
    svc = AlertService(settings)
    svc._client = _mock_client(status_code=200)

    # Exhaust rate limit with INFO messages
    for _ in range(2):
        await svc.send("msg", AlertSeverity.INFO)

    # INFO now rejected
    assert await svc.send("msg", AlertSeverity.INFO) is False

    # CRITICAL still goes through
    result = await svc.send_critical("critical msg")
    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_circuit_breaker_trips_after_max_failures():
    """After max_failures consecutive API errors, is_enabled becomes False."""
    settings = _make_settings()
    svc = AlertService(settings)
    svc._client = _mock_client(status_code=500, text="Internal Server Error")

    # Trigger max_failures (5) failures — use CRITICAL to bypass rate limit
    for _ in range(5):
        await svc.send_critical("failing")

    assert not svc.is_enabled


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reset_circuit_breaker_re_enables_service():
    """reset_circuit_breaker() should restore is_enabled to True."""
    settings = _make_settings()
    svc = AlertService(settings)

    # Manually trip the circuit breaker
    svc._consecutive_failures = svc._max_failures

    assert not svc.is_enabled

    svc.reset_circuit_breaker()

    assert svc.is_enabled


@pytest.mark.unit
@pytest.mark.asyncio
async def test_circuit_breaker_does_not_affect_fresh_service():
    """A fresh service with valid config should have is_enabled True."""
    settings = _make_settings()
    svc = AlertService(settings)
    assert svc.is_enabled
    assert svc._consecutive_failures == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_returns_true_on_200():
    """Successful Telegram API call returns True."""
    settings = _make_settings()
    svc = AlertService(settings)
    svc._client = _mock_client(status_code=200)

    result = await svc.send("test message", AlertSeverity.HIGH)
    assert result is True
    assert svc._consecutive_failures == 0
