from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.db.account_repo import AccountRepository
from app.db.session import TradingSessionLocal
from app.models.trading_combo import TradingCombo
from app.services.account_trader import AccountTrader
from app.services.buy_pause_manager import BuyPauseManager
from app.services.kline_ws_manager import KlineWsManager
from app.services.price_collector import PriceCollector
from app.services.rate_limiter import GlobalRateLimiter

if TYPE_CHECKING:
    from app.utils.encryption import EncryptionManager

logger = logging.getLogger(__name__)

_CB_RECOVERY_INTERVAL = 600  # check every 10 minutes
_CB_COOLDOWN_SEC = 1800      # 30 min after trip before auto-recovery
_CB_MAX_AUTO_RETRIES = 3     # max auto-recovery attempts


class TradingEngine:
    """
    멀티 계정 트레이딩 엔진.
    - Each account runs as independent asyncio task
    - Staggered scheduling: jitter delay per account
    - Shared PriceCollector and GlobalRateLimiter
    """

    def __init__(self, rate_limiter: GlobalRateLimiter, encryption: EncryptionManager):
        self._traders: dict[UUID, AccountTrader] = {}
        self._tasks: dict[UUID, asyncio.Task] = {}
        self._price_collector = PriceCollector()
        self._rate_limiter = rate_limiter
        self._encryption = encryption
        self._kline_ws = KlineWsManager()
        self._price_collector.set_kline_ws(self._kline_ws)
        self._account_symbols: dict[UUID, set[str]] = {}  # account_id -> symbol mapping

    async def start(self):
        """Start trading loops for all active accounts with staggered scheduling"""
        # Start WebSocket kline manager
        await self._kline_ws.start()

        async with TradingSessionLocal() as session:
            repo = AccountRepository(session)
            accounts = await repo.get_active_accounts()

        logger.info(f"Starting trading engine with {len(accounts)} active accounts")

        async def _start_with_jitter(account, index):
            jitter = random.uniform(0, 3.0) + (index * 0.5)
            await asyncio.sleep(jitter)
            try:
                await self.start_account(account.id)
            except Exception as e:
                logger.error(f"Failed to start account {account.id}: {e}")

        await asyncio.gather(
            *[_start_with_jitter(acc, i) for i, acc in enumerate(accounts)]
        )

        # Start background recovery loop
        asyncio.create_task(self._circuit_breaker_recovery_loop())

    async def start_account(self, account_id: UUID):
        if account_id in self._tasks:
            return
        # Single session: circuit breaker check + combo symbols fetch
        symbol = None
        combo_symbols: set[str] = set()
        async with TradingSessionLocal() as session:
            repo = AccountRepository(session)
            account = await repo.get_by_id(account_id)
            if account and (account.circuit_breaker_failures or 0) >= 5:
                logger.warning(f"Account {account_id} has active circuit breaker ({account.circuit_breaker_failures} failures), skipping start")
                return
            if account:
                symbol = account.symbol
            # Fetch combo symbols in the same session
            stmt = select(TradingCombo.symbols).where(
                TradingCombo.account_id == account_id,
                TradingCombo.is_enabled.is_(True),
            )
            result = await session.execute(stmt)
            for row in result.scalars():
                if row:
                    combo_symbols.update(s.lower() for s in row)
        if not combo_symbols and symbol:
            combo_symbols = {symbol.lower()}
        for s in combo_symbols:
            await self._kline_ws.subscribe(s)
        self._account_symbols[account_id] = combo_symbols
        trader = AccountTrader(
            account_id=account_id,
            price_collector=self._price_collector,
            rate_limiter=self._rate_limiter,
            encryption=self._encryption,
            initial_symbols=combo_symbols,
        )
        self._traders[account_id] = trader
        self._tasks[account_id] = asyncio.create_task(
            trader.run_forever(), name=f"trader-{account_id}",
        )
        logger.info(f"Started trader for account {account_id}")

    async def stop_account(self, account_id: UUID):
        if account_id not in self._tasks:
            return
        # Unsubscribe all symbols from kline WS stream
        symbols = self._account_symbols.pop(account_id, set())
        for s in symbols:
            await self._kline_ws.unsubscribe(s)
        trader = self._traders.get(account_id)
        if trader:
            trader.stop()
        task = self._tasks.pop(account_id, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._traders.pop(account_id, None)
        logger.info(f"Stopped trader for account {account_id}")

    async def _get_combo_symbols(self, account_id: UUID) -> set[str]:
        """Collect all unique symbols from active combos for an account."""
        async with TradingSessionLocal() as session:
            stmt = select(TradingCombo.symbols).where(
                TradingCombo.account_id == account_id,
                TradingCombo.is_enabled.is_(True),
            )
            result = await session.execute(stmt)
            all_symbols = set()
            for row in result.scalars():
                if row:
                    all_symbols.update(s.lower() for s in row)
            return all_symbols

    async def refresh_subscriptions(self, account_id: UUID):
        """Recalculate and update kline WS subscriptions for an account's combos."""
        if account_id not in self._traders:
            return  # Account not running, no subscriptions to manage
        new_symbols = await self._get_combo_symbols(account_id)
        old_symbols = self._account_symbols.get(account_id, set())
        # Subscribe new
        for s in new_symbols - old_symbols:
            await self._kline_ws.subscribe(s)
        # Unsubscribe removed
        for s in old_symbols - new_symbols:
            await self._kline_ws.unsubscribe(s)
        self._account_symbols[account_id] = new_symbols
        if new_symbols != old_symbols:
            logger.info(f"Refreshed subscriptions for {account_id}: {old_symbols} -> {new_symbols}")

    async def stop_all(self):
        logger.info("Stopping all traders...")
        account_ids = list(self._tasks.keys())
        for aid in account_ids:
            await self.stop_account(aid)
        # Stop WebSocket kline manager
        await self._kline_ws.stop()

    async def _circuit_breaker_recovery_loop(self):
        """Periodically check CB-tripped accounts and attempt auto-recovery."""
        while True:
            await asyncio.sleep(_CB_RECOVERY_INTERVAL)
            try:
                async with TradingSessionLocal() as session:
                    repo = AccountRepository(session)
                    tripped = await repo.get_circuit_breaker_tripped()
                    for account in tripped:
                        if not account.circuit_breaker_disabled_at:
                            continue
                        elapsed = (datetime.now(UTC) - account.circuit_breaker_disabled_at).total_seconds()
                        if elapsed < _CB_COOLDOWN_SEC:
                            continue
                        if (account.auto_recovery_attempts or 0) >= _CB_MAX_AUTO_RETRIES:
                            continue  # manual intervention needed
                        logger.info("Auto-recovering CB-tripped account %s (attempt %d)", account.id, (account.auto_recovery_attempts or 0) + 1)
                        await repo.reset_circuit_breaker(account.id)
                        await repo.increment_auto_recovery_attempts(account.id)
                        await session.commit()
                        # Remove stale trader/task refs before restarting
                        self._traders.pop(account.id, None)
                        self._tasks.pop(account.id, None)
                        await self.start_account(account.id)
            except Exception as e:
                logger.error("CB recovery loop error: %s", e)

    async def reload_account(self, account_id: UUID):
        await self.stop_account(account_id)
        await self.start_account(account_id)

    async def resume_buying(self, account_id: UUID):
        """Resume buying for a paused account and wake the trading loop."""
        async with TradingSessionLocal() as session:
            mgr = BuyPauseManager(account_id, session)
            await mgr.resume()
            await session.commit()
        # Wake the trader loop from interruptible sleep
        trader = self._traders.get(account_id)
        if trader:
            trader.wake()

    def get_account_health(self) -> dict[str, dict]:
        return {
            str(aid): trader.health_status()
            for aid, trader in self._traders.items()
        }

    @property
    def active_account_count(self) -> int:
        return len(self._traders)

    async def get_current_price(self, symbol: str) -> float:
        """Public accessor for current price from PriceCollector cache."""
        return await self._price_collector.get_price(symbol)

    def get_ws_status(self) -> dict:
        """Public accessor for WebSocket kline manager status."""
        return {
            "healthy": self._kline_ws.is_healthy(),
            "subscriptions": self._kline_ws.subscription_count,
        }
