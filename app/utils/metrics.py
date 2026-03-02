"""Prometheus metrics definitions for crypto-multi-trader."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Trading cycle metrics
TRADING_CYCLE_DURATION = Histogram(
    "trading_cycle_duration_seconds",
    "Step() execution time per account",
    ["account_id"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

ORDER_PLACEMENT_DURATION = Histogram(
    "order_placement_duration_seconds",
    "Exchange API latency for order placement",
    ["side"],  # buy/sell
)

ORDERS_PLACED = Counter(
    "orders_placed_total",
    "Total orders placed",
    ["side", "status"],  # side=buy/sell, status=success/failed
)

CIRCUIT_BREAKER_TRIPS = Counter(
    "circuit_breaker_trips_total",
    "Circuit breaker trip count",
    ["account_id"],
)

BUY_PAUSE_STATE = Gauge(
    "buy_pause_state",
    "Current buy pause state (0=NORMAL, 1=MONITORING, 2=PAUSED, 3=RECOVERING)",
    ["account_id"],
)

WS_MESSAGES_RECEIVED = Counter(
    "ws_messages_received_total",
    "WebSocket messages received",
    ["symbol"],
)

WS_RECONNECTIONS = Counter(
    "ws_reconnections_total",
    "WebSocket reconnection count",
)

BALANCE_USDT = Gauge(
    "balance_usdt",
    "Current USDT balance per account",
    ["account_id"],
)

THREADPOOL_UTILIZATION = Gauge(
    "threadpool_utilization_ratio",
    "ThreadPoolExecutor active/max thread ratio",
)

# Reconciliation
RECON_DRIFT = Counter(
    "reconciliation_drift_total",
    "Reconciliation drift events detected",
    ["account_id"],
)

RECON_RUNS = Counter(
    "reconciliation_runs_total",
    "Total reconciliation runs",
    ["status"],  # ok, drift_detected, error
)

# Active accounts gauge
ACTIVE_ACCOUNTS = Gauge(
    "active_trading_accounts",
    "Number of currently active trading accounts",
)

# Open lots
OPEN_LOTS = Gauge(
    "open_lots_total",
    "Number of open lots",
    ["account_id", "symbol"],
)

# Exchange API
EXCHANGE_API_CALLS = Counter(
    "exchange_api_calls_total",
    "Exchange API calls",
    ["method"],
)

EXCHANGE_API_ERRORS = Counter(
    "exchange_api_errors_total",
    "Exchange API errors",
    ["method", "error_type"],
)

# DB retry
DB_RETRY_ATTEMPTS = Counter(
    "db_retry_attempts_total",
    "Database connection retry attempts",
    ["account_id"],
)

# Auto recovery
AUTO_RECOVERY_ATTEMPTS = Counter(
    "auto_recovery_attempts_total",
    "Circuit breaker auto-recovery attempts",
    ["account_id", "success"],
)
