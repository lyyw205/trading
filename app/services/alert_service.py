"""
Telegram alert service for critical trading events.

Features:
- Rate limiting (max N non-critical alerts per hour)
- Fire-and-forget pattern (never blocks trading loop)
- Severity levels: CRITICAL (immediate), HIGH (debounced), INFO (batched)
- Self-circuit-breaker (stops trying if Telegram API is down)
"""
from __future__ import annotations

import logging
import time
from collections import deque
from enum import StrEnum

import httpx

from app.config import GlobalConfig

logger = logging.getLogger(__name__)


class AlertSeverity(StrEnum):
    CRITICAL = "CRITICAL"   # circuit breaker, account disabled
    HIGH = "HIGH"           # 3+ consecutive failures
    MEDIUM = "MEDIUM"       # buy pause state change
    INFO = "INFO"           # daily digest, status updates


class AlertService:
    """Lightweight Telegram alerting. No external queue needed at this scale."""

    TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, settings: GlobalConfig | None = None):
        self._settings = settings or GlobalConfig()
        self._enabled = bool(
            self._settings.telegram_bot_token and self._settings.telegram_chat_id
        )
        self._rate_limit = self._settings.alert_rate_limit_per_hour
        self._send_times: deque[float] = deque(maxlen=self._rate_limit)
        self._consecutive_failures = 0
        self._max_failures = 5  # circuit breaker for Telegram API itself

    @property
    def is_enabled(self) -> bool:
        return self._enabled and self._consecutive_failures < self._max_failures

    async def send(self, message: str, severity: AlertSeverity = AlertSeverity.INFO) -> bool:
        """
        Send alert via Telegram. Non-blocking, never raises.
        Returns True if sent, False if skipped/failed.
        """
        if not self.is_enabled:
            return False

        # CRITICAL always sends; others are rate-limited
        if severity != AlertSeverity.CRITICAL and not self._check_rate_limit():
            logger.debug("Alert rate-limited: %s", message[:50])
            return False

        prefix = {
            AlertSeverity.CRITICAL: "🚨",
            AlertSeverity.HIGH: "⚠️",
            AlertSeverity.MEDIUM: "📊",
            AlertSeverity.INFO: "ℹ️",
        }.get(severity, "")

        formatted = f"{prefix} [{severity.value}] {message}"

        try:
            return await self._send_telegram(formatted)
        except Exception as e:
            logger.warning("Alert send failed: %s", e)
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_failures:
                logger.error(
                    "Alert service circuit breaker tripped after %d failures",
                    self._max_failures,
                )
            return False

    async def send_critical(self, message: str) -> bool:
        """Immediate send for circuit breaker, account disable events."""
        return await self.send(message, AlertSeverity.CRITICAL)

    async def send_high(self, message: str) -> bool:
        """Rate-limited send for repeated failures."""
        return await self.send(message, AlertSeverity.HIGH)

    async def send_medium(self, message: str) -> bool:
        """Rate-limited send for state changes."""
        return await self.send(message, AlertSeverity.MEDIUM)

    async def _send_telegram(self, text: str) -> bool:
        """Actually send to Telegram API with 5-second timeout."""
        url = self.TELEGRAM_API.format(token=self._settings.telegram_bot_token)
        payload = {
            "chat_id": self._settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                self._consecutive_failures = 0  # reset on success
                self._send_times.append(time.monotonic())
                return True
            else:
                logger.warning(
                    "Telegram API returned %d: %s",
                    response.status_code,
                    response.text[:100],
                )
                self._consecutive_failures += 1
                return False

    def _check_rate_limit(self) -> bool:
        """Check if we're within the rate limit window."""
        now = time.monotonic()
        # Remove entries older than 1 hour
        while self._send_times and now - self._send_times[0] > 3600:
            self._send_times.popleft()
        return len(self._send_times) < self._rate_limit

    def reset_circuit_breaker(self) -> None:
        """Manual reset of the alert circuit breaker."""
        self._consecutive_failures = 0
        logger.info("Alert service circuit breaker reset")


# Module-level singleton (lazy initialization)
_alert_service: AlertService | None = None


def get_alert_service() -> AlertService:
    """Get or create the global AlertService instance."""
    global _alert_service
    if _alert_service is None:
        _alert_service = AlertService()
    return _alert_service
