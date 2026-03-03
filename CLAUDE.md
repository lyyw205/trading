# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Crypto Multi-Trader: FastAPI-based multi-account cryptocurrency automated trading bot (Python 3.12, PostgreSQL 16, Binance API). Korean-language documentation and comments are common throughout the codebase.

## Commands

```bash
# Dev setup
make dev-install          # pip install -e ".[dev]" + pre-commit hooks

# Testing (requires test DB on port 5433)
make test-db-up           # Start ephemeral test PostgreSQL via Docker
make test                 # pytest -m "not slow"
make test-unit            # pytest -m unit
make test-integration     # pytest -m integration
make test-all             # Full suite with coverage
pytest tests/unit/test_buy_pause.py -v  # Run single test file
pytest -k "test_sizing" -v              # Run tests matching pattern

# Lint & format
make lint                 # ruff check app/ tests/
make format               # ruff format app/ tests/

# Database migrations
alembic upgrade head      # Apply all migrations
alembic revision --autogenerate -m "description"  # New migration

# Run the app
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Architecture

**Layered design** — API routers → service layer → strategy plugins → exchange abstraction → repositories → ORM models → PostgreSQL.

### Core Trading Loop
`TradingEngine` spawns one `AccountTrader` asyncio task per active account. Each trader loops on an interval, iterating over combo×symbol pairs: `BuyLogic.pre_tick()` → `SellLogic.tick()` → `BuyLogic.tick()`. `BuyPauseManager` governs ACTIVE→THROTTLED→PAUSED state transitions based on balance. `KlineWsManager` maintains Binance WebSocket streams with refcount-based subscriptions and REST backfill. `CandleAggregator` runs a 6-hour background loop compressing 1m→5m→1h→1d candles.

### Strategy Plugin System
Strategies register via `@register` decorator in `app/strategies/buys/` and `app/strategies/sells/`. Each receives an immutable `StrategyContext` per tick and persists state through `StrategyStateStore` (KV scoped to `{combo_id}:{symbol}`). Current strategies: `lot_stacking` (bucket-based step buys), `trend` (recenter-based entry), `fixed_tp` (take-profit sells).

### Exchange Abstraction
`ExchangeClient` base in `app/exchange/base_client.py` with implementations: `BinanceClient` (real API via `asyncio.to_thread`), `BacktestClient` (in-memory simulation), `FaultyBacktestClient` (fault injection for testing).

### Key Patterns
- **Concurrency**: async/await throughout, per-account asyncio tasks, ThreadPoolExecutor for sync Binance calls
- **Error handling**: circuit breaker (5 failures → auto-disable), exponential backoff (max 60s), error classification enum
- **Security**: Fernet encryption for API keys with rotation, bcrypt auth, signed session cookies, CSRF middleware
- **DB sessions**: `app/db/session.py` — async SQLAlchemy with pool_size=15, slow query detection (>200ms)
- **DI**: `app/dependencies.py` provides `get_db`, `get_current_user`, `require_admin`, `get_owned_account`
- **SSR frontend**: Jinja2 templates in `app/dashboard/templates/`, static files in `app/dashboard/static/`
- **Backtest engine**: Isolated module in `backtest/` using in-memory repositories (no DB dependency)

## Code Style

- **Ruff**: line length 120, Python 3.12 target, rules E/F/W/I/UP/B/SIM
- **Ignored**: E501 (line length), B008 (FastAPI `Depends`), B904 (`raise from`)
- **Type hints** used throughout
- **Async by default** for all I/O operations
- **Test markers**: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.slow`, `@pytest.mark.exchange`
- **Test DB**: SAVEPOINT-based rollback per test (no manual cleanup needed)

## Environment

Copy `.env.example` to `.env`. Key variables: `DATABASE_URL`, `ENCRYPTION_KEYS` (comma-separated Fernet keys, first is latest), `SESSION_SECRET_KEY`, `CSRF_SECRET`. Dev mode auto-generates secrets with warnings. Test DB: `postgresql+asyncpg://test:test@localhost:5433/crypto_trader_test`.
