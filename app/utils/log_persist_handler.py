"""Custom logging handler that persists ERROR/CRITICAL logs and trade events to database via queue."""

from __future__ import annotations

import logging
import queue
import re
import threading
import uuid
from datetime import UTC, datetime

from app.utils.logging import current_account_id, current_cycle_id

# Logger names whose records should never be persisted (recursion guard).
# Using logger name (dot-separated) instead of record.module (filename-based)
# to be resilient to file renames.
_SKIP_LOGGER_PREFIXES = (
    "app.services.log_persister",
    "app.services.daily_report_service",
    "app.utils.log_persist_handler",
    "app.db.session",
)

# Trade event patterns that should be persisted even at INFO/WARNING level.
_TRADE_EVENT_RE = re.compile(
    "|".join(
        (
            # Buy/sell fills
            r"buy filled",
            r"TP filled",
            # Order placement
            r"placed .+ order",
            # Order failures
            r"place .+ failed",
            # Buy pause state changes
            r"Buy pause",
            r"pause cleared",
            r"pause manually resumed",
            r"pause forced",
            # Circuit breaker
            r"Circuit breaker",
            r"Auto-recovering CB",
            # Sell order status
            r"sell order .+ (CANCELED|EXPIRED)",
            # (스캔 중 로그는 인메모리 버퍼에만 유지, DB 저장 불필요)
            # Trading loop lifecycle
            r"트레이딩 루프가 정상 시작",
        )
    )
)

# Thread-local reentrance guard to prevent infinite recursion
_reentrant = threading.local()

# Patterns to mask sensitive data in log messages and stack traces
_SENSITIVE_PATTERNS = re.compile(
    r"(?i)"
    r"("
    r"(?:api[_-]?key|api[_-]?secret|secret[_-]?key|password|token|authorization)"
    r"\s*[:=]\s*"
    r")"
    r"['\"]?([A-Za-z0-9+/=_\-]{8,})['\"]?",
)


def _mask_sensitive(text: str | None) -> str | None:
    """Replace sensitive values (API keys, secrets, tokens) with masked version."""
    if not text:
        return text
    return _SENSITIVE_PATTERNS.sub(r"\1***MASKED***", text)


class PersistLogHandler(logging.Handler):
    """Thread-safe handler that queues ERROR+ records for async DB persistence.

    Uses queue.Queue (not asyncio.Queue) because logging can happen from
    ThreadPoolExecutor threads (e.g., Binance sync API calls).
    """

    def __init__(self, maxsize: int = 10000) -> None:
        super().__init__(level=logging.INFO)
        self.log_queue: queue.Queue[dict] = queue.Queue(maxsize=maxsize)
        self._drop_count = 0
        self._drop_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        # Recursion guard: skip loggers from DB-touching modules
        if record.name.startswith(_SKIP_LOGGER_PREFIXES):
            return

        # INFO/WARNING: only persist if it matches a trade event pattern
        if record.levelno < logging.ERROR:
            msg = record.getMessage()
            if not _TRADE_EVENT_RE.search(msg):
                return
        # Thread-local reentrance guard
        if getattr(_reentrant, "in_emit", False):
            return

        _reentrant.in_emit = True
        try:
            # Map "system" account_id to None for DB storage
            acct = current_account_id.get()
            account_id = None
            if acct and acct != "system" and acct != "-":
                try:
                    account_id = uuid.UUID(acct)
                except ValueError:
                    account_id = None

            entry = {
                "logged_at": datetime.fromtimestamp(record.created, tz=UTC),
                "level": record.levelname,
                "account_id": account_id,
                "module": record.module[:100] if record.module else None,
                "message": _mask_sensitive(record.getMessage()),
                "exception": _mask_sensitive(self.formatException(record.exc_info) if record.exc_info else None),
                "extra": {},
            }

            # Add cycle_id if available
            cycle_id = current_cycle_id.get()
            if cycle_id and cycle_id != "-":
                entry["extra"]["cycle_id"] = cycle_id

            # Add duration_ms if available
            duration_ms = getattr(record, "duration_ms", None)
            if duration_ms is not None:
                entry["extra"]["duration_ms"] = duration_ms

            # Clean up empty extra
            if not entry["extra"]:
                entry["extra"] = None

            self.log_queue.put_nowait(entry)
        except queue.Full:
            with self._drop_lock:
                self._drop_count += 1
                count = self._drop_count
            if count % 100 == 1:
                # Use stderr to avoid recursion
                import sys

                print(f"PersistLogHandler: queue full, dropped {count} entries", file=sys.stderr)
        except Exception:
            self.handleError(record)
        finally:
            _reentrant.in_emit = False
