"""In-memory request metrics collector.

Tracks per-endpoint call count, total response time, and error count.
No DB writes — purely in-memory dictionary.
"""

from __future__ import annotations

import re
import threading
import time


class EndpointStats:
    __slots__ = ("call_count", "total_ms", "error_count")

    def __init__(self) -> None:
        self.call_count = 0
        self.total_ms = 0.0
        self.error_count = 0


# Normalize UUID-like path segments to placeholders
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"/\d+(?=/|$)")


class RequestMetricsCollector:
    """Singleton-style in-memory request metrics."""

    def __init__(self) -> None:
        self._data: dict[str, EndpointStats] = {}
        self._lock = threading.Lock()
        self._start_time = time.monotonic()

    def _normalize_path(self, path: str) -> str:
        path = _UUID_RE.sub("{id}", path)
        path = _NUMERIC_RE.sub("/{id}", path)
        return path

    def record(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        key = f"{method} {self._normalize_path(path)}"
        with self._lock:
            stats = self._data.get(key)
            if stats is None:
                stats = EndpointStats()
                self._data[key] = stats
            stats.call_count += 1
            stats.total_ms += duration_ms
            if status_code >= 400:
                stats.error_count += 1

    def get_overview(self) -> dict:
        with self._lock:
            total_calls = sum(s.call_count for s in self._data.values())
            total_ms = sum(s.total_ms for s in self._data.values())
            total_errors = sum(s.error_count for s in self._data.values())
        return {
            "total_requests": total_calls,
            "avg_response_ms": round(total_ms / total_calls, 1) if total_calls > 0 else 0,
            "error_rate": round(total_errors / total_calls, 4) if total_calls > 0 else 0,
            "uptime_seconds": round(time.monotonic() - self._start_time),
        }

    def get_summary(self, top_n: int = 20) -> list[dict]:
        with self._lock:
            items = [
                {
                    "endpoint": key,
                    "calls": s.call_count,
                    "avg_ms": round(s.total_ms / s.call_count, 1) if s.call_count > 0 else 0,
                    "error_rate": round(s.error_count / s.call_count, 4) if s.call_count > 0 else 0,
                    "errors": s.error_count,
                }
                for key, s in self._data.items()
            ]
        items.sort(key=lambda x: x["calls"], reverse=True)
        return items[:top_n]

    def reset(self) -> None:
        with self._lock:
            self._data.clear()
            self._start_time = time.monotonic()


# Module-level singleton
request_metrics = RequestMetricsCollector()
