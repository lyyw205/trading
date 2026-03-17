"""Shared contextvars for correlation IDs across logging and handlers."""

from __future__ import annotations

import contextvars

current_account_id: contextvars.ContextVar[str] = contextvars.ContextVar("account_id", default="system")
current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
current_cycle_id: contextvars.ContextVar[str] = contextvars.ContextVar("cycle_id", default="-")
