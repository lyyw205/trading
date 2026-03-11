from __future__ import annotations

from pydantic import BaseModel


class DatabaseCheck(BaseModel):
    status: str
    latency_ms: float | None = None
    error: str | None = None


class HealthCheckResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    checks: dict[str, DatabaseCheck]
    alerts: list[str]
