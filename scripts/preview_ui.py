#!/usr/bin/env python3
"""
[SAMPLE_DATA] UI Preview Server
=================================
Lightweight server for previewing the web UI design without requiring
database, authentication, or trading engine infrastructure.

All sample data in this file is marked with [SAMPLE_DATA].
To clean up: just delete this file (scripts/preview_ui.py).
No other files in the project are modified.

Usage:
    python scripts/preview_ui.py
    # Open http://localhost:8080

Pages:
    /login              - Login page (Google OAuth UI)
    /accounts           - Account list
    /accounts/<id>      - Account detail dashboard (chart, lots, tune, etc.)
    /admin              - Admin panel (system health, users, all accounts)
"""
from __future__ import annotations

import os
import sys
import time
import random
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ============================================================
# [SAMPLE_DATA] --- Mock data starts here ---
# Everything between this line and the "Mock data ends" marker
# is sample data for UI preview only.
# ============================================================

random.seed(42)  # [SAMPLE_DATA] Fixed seed for consistent preview

# [SAMPLE_DATA] Mock user (injected into all pages, bypasses auth)
SAMPLE_USER = {
    "id": "00000000-0000-0000-0000-000000000001",
    "email": "demo@example.com",
    "role": "admin",
}

# [SAMPLE_DATA] Mock accounts (3 accounts with different states)
SAMPLE_ACCOUNTS = [
    {
        "id": "aaaaaaaa-1111-2222-3333-444444444444",
        "label": "Main BTC Account",
        "name": "Main BTC Account",
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "exchange": "binance",
        "is_active": True,
        "circuit_breaker_tripped": False,
        "circuit_breaker_failures": 0,
        "circuit_breaker_disabled_at": None,
        "last_success_at": datetime.now().isoformat(),
        "loop_interval_sec": 60,
        "order_cooldown_sec": 7,
        "created_at": (datetime.now() - timedelta(days=30)).isoformat(),
        "updated_at": datetime.now().isoformat(),
        "health_status": "healthy",
        "owner_email": "demo@example.com",
        "user_id": "00000000-0000-0000-0000-000000000001",
    },
    {
        "id": "bbbbbbbb-1111-2222-3333-444444444444",
        "label": "ETH Stacking",
        "name": "ETH Stacking",
        "symbol": "ETHUSDT",
        "base_asset": "ETH",
        "quote_asset": "USDT",
        "exchange": "binance",
        "is_active": True,
        "circuit_breaker_tripped": False,
        "circuit_breaker_failures": 0,
        "circuit_breaker_disabled_at": None,
        "last_success_at": datetime.now().isoformat(),
        "loop_interval_sec": 60,
        "order_cooldown_sec": 7,
        "created_at": (datetime.now() - timedelta(days=15)).isoformat(),
        "updated_at": datetime.now().isoformat(),
        "health_status": "healthy",
        "owner_email": "demo@example.com",
        "user_id": "00000000-0000-0000-0000-000000000001",
    },
    {
        "id": "cccccccc-1111-2222-3333-444444444444",
        "label": "Aggressive BTC",
        "name": "Aggressive BTC",
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "exchange": "binance",
        "is_active": False,
        "circuit_breaker_tripped": True,
        "circuit_breaker_failures": 5,
        "circuit_breaker_disabled_at": datetime.now().isoformat(),
        "last_success_at": (datetime.now() - timedelta(hours=2)).isoformat(),
        "loop_interval_sec": 30,
        "order_cooldown_sec": 5,
        "created_at": (datetime.now() - timedelta(days=45)).isoformat(),
        "updated_at": datetime.now().isoformat(),
        "health_status": "circuit_breaker_tripped",
        "owner_email": "trader2@example.com",
        "user_id": "00000000-0000-0000-0000-000000000002",
    },
]


def _generate_candles() -> list[dict]:
    """[SAMPLE_DATA] Generate realistic BTC 1h candle data (7 days)."""
    candles = []
    now = int(time.time())
    start = now - (7 * 24 * 3600)  # 7 days ago
    price = 96500.0

    for i in range(7 * 24):
        ts = start + i * 3600
        change_pct = random.gauss(0, 0.003)
        open_price = price
        high = open_price * (1 + abs(random.gauss(0, 0.004)))
        low = open_price * (1 - abs(random.gauss(0, 0.004)))
        close = open_price * (1 + change_pct)
        # Ensure OHLC consistency
        high = max(high, open_price, close) * (1 + random.uniform(0, 0.001))
        low = min(low, open_price, close) * (1 - random.uniform(0, 0.001))
        candles.append({
            "time": ts,
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
        })
        price = close
    return candles


def _generate_trade_events(candles: list[dict]) -> list[dict]:
    """[SAMPLE_DATA] Generate buy/sell markers on random candles."""
    events = []
    i = 0
    while i < len(candles):
        c = candles[i]
        side = "buy" if random.random() < 0.6 else "sell"
        events.append({
            "time": c["time"],
            "side": side,
            "price": round(c["close"], 2),
        })
        i += random.randint(8, 24)  # Skip 8-24 candles between events
    return events


# [SAMPLE_DATA] Pre-generate chart data
SAMPLE_CANDLES = _generate_candles()
SAMPLE_TRADE_EVENTS = _generate_trade_events(SAMPLE_CANDLES)

# [SAMPLE_DATA] Current price from last generated candle
CURRENT_PRICE = SAMPLE_CANDLES[-1]["close"] if SAMPLE_CANDLES else 97000.0

# [SAMPLE_DATA] Asset status panel data
SAMPLE_ASSET_STATUS = {
    "btc_balance": 0.152340,
    "usdt_balance": 4521.38,
    "reserve_pool_usdt": 1200.00,
    "reserve_pool_pct": 12.5,
    "total_invested_usdt": 8750.42,
}

# [SAMPLE_DATA] Open lots (mix of lot_stacking and trend_buy)
SAMPLE_LOTS = [
    {
        "strategy": "lot_stacking",
        "buy_price": 95200.50,
        "qty": 0.001050,
        "cost_usdt": 99.96,
        "current_price": CURRENT_PRICE,
        "pnl_pct": round((CURRENT_PRICE - 95200.50) / 95200.50 * 100, 2),
        "sell_order_status": "LIMIT_PLACED",
    },
    {
        "strategy": "lot_stacking",
        "buy_price": 94800.00,
        "qty": 0.001055,
        "cost_usdt": 100.01,
        "current_price": CURRENT_PRICE,
        "pnl_pct": round((CURRENT_PRICE - 94800.00) / 94800.00 * 100, 2),
        "sell_order_status": "LIMIT_PLACED",
    },
    {
        "strategy": "lot_stacking",
        "buy_price": 94350.25,
        "qty": 0.001060,
        "cost_usdt": 100.01,
        "current_price": CURRENT_PRICE,
        "pnl_pct": round((CURRENT_PRICE - 94350.25) / 94350.25 * 100, 2),
        "sell_order_status": "PENDING",
    },
    {
        "strategy": "lot_stacking",
        "buy_price": 96800.00,
        "qty": 0.001033,
        "cost_usdt": 99.99,
        "current_price": CURRENT_PRICE,
        "pnl_pct": round((CURRENT_PRICE - 96800.00) / 96800.00 * 100, 2),
        "sell_order_status": "LIMIT_PLACED",
    },
    {
        "strategy": "lot_stacking",
        "buy_price": 97100.75,
        "qty": 0.001030,
        "cost_usdt": 100.01,
        "current_price": CURRENT_PRICE,
        "pnl_pct": round((CURRENT_PRICE - 97100.75) / 97100.75 * 100, 2),
        "sell_order_status": "LIMIT_PLACED",
    },
    {
        "strategy": "trend_buy",
        "buy_price": 93500.00,
        "qty": 0.002139,
        "cost_usdt": 200.00,
        "current_price": CURRENT_PRICE,
        "pnl_pct": round((CURRENT_PRICE - 93500.00) / 93500.00 * 100, 2),
        "sell_order_status": "LIMIT_PLACED",
    },
    {
        "strategy": "trend_buy",
        "buy_price": 92800.00,
        "qty": 0.002155,
        "cost_usdt": 200.00,
        "current_price": CURRENT_PRICE,
        "pnl_pct": round((CURRENT_PRICE - 92800.00) / 92800.00 * 100, 2),
        "sell_order_status": "LIMIT_PLACED",
    },
    {
        "strategy": "trend_buy",
        "buy_price": 97200.00,
        "qty": 0.002058,
        "cost_usdt": 200.02,
        "current_price": CURRENT_PRICE,
        "pnl_pct": round((CURRENT_PRICE - 97200.00) / 97200.00 * 100, 2),
        "sell_order_status": "PENDING",
    },
]

# [SAMPLE_DATA] Strategy tune parameters
SAMPLE_TUNE = {
    "lot_stacking": {
        "drop_pct": 0.5,
        "tp_pct": 1.0,
        "buy_usdt": 100,
        "prebuy_pct": 0.3,
        "cancel_rebound_pct": 0.2,
        "recenter_pct": 1.0,
        "recenter_ema_n": 20,
        "recenter_enabled": True,
    },
    "trend_buy": {
        "drop_pct": 1.0,
        "tp_pct": 2.0,
        "buy_usdt": 200,
        "step_pct": 0.5,
        "step_count": 3,
        "above_base_pct": 0.5,
    },
}

# [SAMPLE_DATA] Admin user list
SAMPLE_USERS = [
    {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "demo@example.com",
        "role": "admin",
        "created_at": (datetime.now() - timedelta(days=60)).isoformat(),
    },
    {
        "id": "00000000-0000-0000-0000-000000000002",
        "email": "trader2@example.com",
        "role": "user",
        "created_at": (datetime.now() - timedelta(days=30)).isoformat(),
    },
    {
        "id": "00000000-0000-0000-0000-000000000003",
        "email": "viewer@example.com",
        "role": "user",
        "created_at": (datetime.now() - timedelta(days=10)).isoformat(),
    },
]

# [SAMPLE_DATA] Admin overview health data
SAMPLE_ADMIN_OVERVIEW = {
    "total_users": 3,
    "total_accounts": 3,
    "active_traders": 2,
}

# ============================================================
# [SAMPLE_DATA] --- Mock data ends here ---
# ============================================================


# ============================================================
# [SAMPLE_DATA] Preview FastAPI App
# No auth middleware, no DB, no trading engine.
# Serves the real templates + static files with mock API data.
# ============================================================

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="[SAMPLE_DATA] UI Preview")

# Serve the real static files (CSS/JS)
static_dir = os.path.join(PROJECT_ROOT, "app", "dashboard", "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Use the real Jinja2 templates
templates_dir = os.path.join(PROJECT_ROOT, "app", "dashboard", "templates")
templates = Jinja2Templates(directory=templates_dir)


# ---- [SAMPLE_DATA] Page routes (bypass auth, inject mock user) ----

@app.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse(url="/accounts")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    # [SAMPLE_DATA] Inject mock user (no auth check)
    return templates.TemplateResponse("accounts.html", {
        "request": request,
        "user": SAMPLE_USER,
    })


@app.get("/accounts/{account_id}", response_class=HTMLResponse)
async def account_detail_page(request: Request, account_id: str):
    # [SAMPLE_DATA] Inject mock user (no auth check)
    return templates.TemplateResponse("account_detail.html", {
        "request": request,
        "user": SAMPLE_USER,
        "account_id": account_id,
    })


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    # [SAMPLE_DATA] Inject mock admin user (no role check)
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "user": SAMPLE_USER,
    })


@app.get("/admin/backtest/{backtest_id}", response_class=HTMLResponse)
async def backtest_report_page(request: Request, backtest_id: str):
    # [SAMPLE_DATA] Inject mock admin user (no role check)
    return templates.TemplateResponse("backtest_report.html", {
        "request": request,
        "user": SAMPLE_USER,
        "backtest_id": backtest_id,
    })


# ---- [SAMPLE_DATA] Mock API endpoints ----

@app.get("/api/accounts")
async def api_list_accounts():
    """[SAMPLE_DATA] Mock account list."""
    return SAMPLE_ACCOUNTS


@app.get("/api/accounts/{account_id}")
async def api_get_account(account_id: str):
    """[SAMPLE_DATA] Mock single account."""
    for acct in SAMPLE_ACCOUNTS:
        if acct["id"] == account_id:
            return acct
    # Fallback to first account for any unknown ID
    return SAMPLE_ACCOUNTS[0]


@app.get("/api/dashboard/{account_id}/price_candles")
async def api_price_candles(account_id: str):
    """[SAMPLE_DATA] Mock BTC 1h candle data (7 days)."""
    return SAMPLE_CANDLES


@app.get("/api/dashboard/{account_id}/trade_events")
async def api_trade_events(account_id: str):
    """[SAMPLE_DATA] Mock buy/sell trade event markers."""
    return SAMPLE_TRADE_EVENTS


@app.get("/api/dashboard/{account_id}/asset_status")
async def api_asset_status(account_id: str):
    """[SAMPLE_DATA] Mock asset balance/reserve data."""
    return SAMPLE_ASSET_STATUS


@app.get("/api/dashboard/{account_id}/lots")
async def api_lots(account_id: str):
    """[SAMPLE_DATA] Mock open lot table data."""
    return SAMPLE_LOTS


@app.get("/api/dashboard/{account_id}/tune")
async def api_get_tune(account_id: str):
    """[SAMPLE_DATA] Mock strategy tune parameters."""
    return SAMPLE_TUNE


@app.post("/api/dashboard/{account_id}/tune")
async def api_update_tune(account_id: str):
    """[SAMPLE_DATA] Mock tune update (no-op, always succeeds)."""
    return {"status": "updated", "params": {}}


@app.get("/api/admin/overview")
async def api_admin_overview():
    """[SAMPLE_DATA] Mock admin system health overview."""
    return SAMPLE_ADMIN_OVERVIEW


@app.get("/api/admin/accounts")
async def api_admin_accounts():
    """[SAMPLE_DATA] Mock admin accounts list."""
    return SAMPLE_ACCOUNTS


@app.get("/api/admin/users")
async def api_admin_users():
    """[SAMPLE_DATA] Mock admin users list."""
    return SAMPLE_USERS


@app.post("/api/accounts/{account_id}/reset-circuit-breaker")
async def api_reset_circuit_breaker(account_id: str):
    """[SAMPLE_DATA] Mock circuit breaker reset (no-op)."""
    return {"status": "reset", "account_id": account_id}


@app.post("/api/auth/logout")
async def api_logout():
    """[SAMPLE_DATA] Mock logout (no-op)."""
    return {"status": "logged_out"}


@app.put("/api/admin/users/{user_id}/role")
async def api_change_user_role(user_id: str):
    """[SAMPLE_DATA] Mock role change (no-op)."""
    return {"status": "updated"}


# ---- [SAMPLE_DATA] Mock Backtest API endpoints ----

import uuid as _uuid

# [SAMPLE_DATA] In-memory backtest store
_mock_backtests: list[dict] = []


def _generate_backtest_candles(start_ts_ms: int, end_ts_ms: int) -> list[dict]:
    """[SAMPLE_DATA] Generate mock 5m candles for backtest report."""
    candles = []
    price = 95000.0
    ts = start_ts_ms
    while ts <= end_ts_ms:
        change = random.gauss(0, 0.002)
        o = price
        c = price * (1 + change)
        h = max(o, c) * (1 + abs(random.gauss(0, 0.001)))
        l = min(o, c) * (1 - abs(random.gauss(0, 0.001)))
        candles.append({
            "time": int(ts / 1000),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
        })
        price = c
        ts += 300_000  # 5 minutes
    return candles


def _generate_mock_result(start_ts_ms: int, end_ts_ms: int, strategies: list, strategy_params: dict):
    """[SAMPLE_DATA] Generate a mock completed backtest result."""
    initial = 10000.0
    pnl_pct = round(random.uniform(-5, 15), 2)
    final_value = round(initial * (1 + pnl_pct / 100), 2)
    total_trades = random.randint(10, 80)
    winning = random.randint(int(total_trades * 0.4), int(total_trades * 0.8))
    losing = total_trades - winning

    trade_log = []
    ts = start_ts_ms
    for i in range(total_trades):
        side = "BUY" if i % 2 == 0 else "SELL"
        price = round(random.uniform(90000, 100000), 2)
        qty = round(random.uniform(0.0005, 0.003), 6)
        trade_log.append({
            "ts_ms": ts,
            "side": side,
            "price": str(price),
            "qty": str(qty),
            "quote_qty": str(round(price * qty, 2)),
            "strategy": random.choice(strategies),
        })
        ts += random.randint(300_000, 3_600_000)

    equity_curve = []
    eq_val = initial
    ts = start_ts_ms
    while ts <= end_ts_ms:
        eq_val *= (1 + random.gauss(0, 0.001))
        equity_curve.append({"ts_ms": ts, "value": round(eq_val, 2)})
        ts += 3_600_000  # 1 hour

    return {
        "summary": {
            "final_value_usdt": final_value,
            "pnl_usdt": round(final_value - initial, 2),
            "pnl_pct": pnl_pct,
            "total_trades": total_trades,
            "winning_trades": winning,
            "losing_trades": losing,
            "win_rate": round(winning / total_trades * 100, 2) if total_trades else 0,
            "max_drawdown_pct": round(-random.uniform(1, 8), 2),
            "profit_factor": round(random.uniform(0.8, 3.0), 2),
        },
        "trade_log": trade_log,
        "equity_curve": equity_curve,
        "strategy_params": strategy_params,
    }


@app.post("/api/backtest/run")
async def api_backtest_run(request: Request):
    """[SAMPLE_DATA] Mock start backtest — instantly 'completes'."""
    body = await request.json()
    run_id = str(_uuid.uuid4())
    strategies = body.get("strategies", ["lot_stacking"])
    strategy_params = body.get("strategy_params", {})
    start_ts_ms = body.get("start_ts_ms", 0)
    end_ts_ms = body.get("end_ts_ms", 0)
    initial_usdt = body.get("initial_usdt", 10000)

    result = _generate_mock_result(start_ts_ms, end_ts_ms, strategies, strategy_params)

    entry = {
        "id": run_id,
        "symbol": body.get("symbol", "BTCUSDT"),
        "strategies": strategies,
        "strategy_params": strategy_params,
        "initial_usdt": initial_usdt,
        "start_ts_ms": start_ts_ms,
        "end_ts_ms": end_ts_ms,
        "status": "COMPLETED",
        "pnl_pct": result["summary"]["pnl_pct"],
        "created_at": datetime.now().isoformat(),
        "result": result,
    }
    _mock_backtests.insert(0, entry)
    return {"id": run_id, "status": "COMPLETED"}


@app.get("/api/backtest/{run_id}/status")
async def api_backtest_status(run_id: str):
    """[SAMPLE_DATA] Mock backtest status."""
    for bt in _mock_backtests:
        if bt["id"] == run_id:
            return {"id": run_id, "status": bt["status"], "error_message": None}
    return {"id": run_id, "status": "COMPLETED", "error_message": None}


@app.get("/api/backtest/{run_id}/report")
async def api_backtest_report(run_id: str):
    """[SAMPLE_DATA] Mock backtest report."""
    for bt in _mock_backtests:
        if bt["id"] == run_id:
            candles = _generate_backtest_candles(bt["start_ts_ms"], bt["end_ts_ms"])
            return {
                "id": run_id,
                "config": {
                    "symbol": bt["symbol"],
                    "strategies": bt["strategies"],
                    "strategy_params": bt["strategy_params"],
                    "initial_usdt": bt["initial_usdt"],
                    "start_ts_ms": bt["start_ts_ms"],
                    "end_ts_ms": bt["end_ts_ms"],
                },
                "summary": bt["result"]["summary"],
                "trade_log": bt["result"]["trade_log"],
                "equity_curve": bt["result"]["equity_curve"],
                "candles": candles,
            }
    return JSONResponse({"detail": "Not found"}, status_code=404)


@app.get("/api/backtest/list")
async def api_backtest_list():
    """[SAMPLE_DATA] Mock backtest history list."""
    return [
        {
            "id": bt["id"],
            "symbol": bt["symbol"],
            "strategies": bt["strategies"],
            "initial_usdt": bt["initial_usdt"],
            "start_ts_ms": bt["start_ts_ms"],
            "end_ts_ms": bt["end_ts_ms"],
            "status": bt["status"],
            "pnl_pct": bt.get("pnl_pct"),
            "created_at": bt["created_at"],
        }
        for bt in _mock_backtests
    ]


@app.delete("/api/backtest/{run_id}")
async def api_backtest_delete(run_id: str):
    """[SAMPLE_DATA] Mock delete backtest."""
    global _mock_backtests
    _mock_backtests = [bt for bt in _mock_backtests if bt["id"] != run_id]
    return {"status": "deleted", "id": run_id}


# ============================================================
# [SAMPLE_DATA] Entry point
# ============================================================

if __name__ == "__main__":
    import uvicorn

    print()
    print("=" * 54)
    print("  [SAMPLE_DATA] UI Preview Server")
    print("  http://localhost:8080")
    print()
    print("  /login                - Login page")
    print("  /accounts             - Account list (3 accounts)")
    print("  /accounts/<id>        - Account detail dashboard")
    print("  /admin                - Admin panel")
    print()
    print("  To clean up: delete scripts/preview_ui.py")
    print("=" * 54)
    print()

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
