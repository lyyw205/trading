from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING
from uuid import UUID

from app.db.account_repo import AccountRepository
from app.db.session import TradingSessionLocal
from app.services.account_trader import AccountTrader
from app.services.kline_ws_manager import KlineWsManager
from app.services.price_collector import PriceCollector
from app.services.rate_limiter import GlobalRateLimiter

if TYPE_CHECKING:
    from app.utils.encryption import EncryptionManager

logger = logging.getLogger(__name__)


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
        self._account_symbols: dict[UUID, str] = {}  # account_id -> symbol mapping

    async def start(self):
        """Start trading loops for all active accounts with staggered scheduling"""
        # Start WebSocket kline manager
        await self._kline_ws.start()

        async with TradingSessionLocal() as session:
            repo = AccountRepository(session)
            accounts = await repo.get_active_accounts()

        logger.info(f"Starting trading engine with {len(accounts)} active accounts")
        for i, account in enumerate(accounts):
            jitter = random.uniform(0, 3.0) + (i * 0.5)
            await asyncio.sleep(jitter)
            try:
                await self.start_account(account.id)
            except Exception as e:
                logger.error(f"Failed to start account {account.id}: {e}")

    async def start_account(self, account_id: UUID):
        if account_id in self._tasks:
            return
        # Phase 3-C: 서킷 브레이커 상태 확인 후 시작 차단
        symbol = None
        async with TradingSessionLocal() as session:
            repo = AccountRepository(session)
            account = await repo.get_by_id(account_id)
            if account and (account.circuit_breaker_failures or 0) >= 5:
                logger.warning(f"Account {account_id} has active circuit breaker ({account.circuit_breaker_failures} failures), skipping start")
                return
            if account:
                symbol = account.symbol
        # Subscribe symbol to kline WS stream
        if symbol:
            self._account_symbols[account_id] = symbol
            await self._kline_ws.subscribe(symbol)
        trader = AccountTrader(
            account_id=account_id,
            price_collector=self._price_collector,
            rate_limiter=self._rate_limiter,
            encryption=self._encryption,
        )
        self._traders[account_id] = trader
        self._tasks[account_id] = asyncio.create_task(
            trader.run_forever(), name=f"trader-{account_id}",
        )
        logger.info(f"Started trader for account {account_id}")

    async def stop_account(self, account_id: UUID):
        if account_id not in self._tasks:
            return
        # Unsubscribe symbol from kline WS stream
        symbol = self._account_symbols.pop(account_id, None)
        if symbol:
            await self._kline_ws.unsubscribe(symbol)
        trader = self._traders.get(account_id)
        if trader:
            trader.stop()
        task = self._tasks.pop(account_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._traders.pop(account_id, None)
        logger.info(f"Stopped trader for account {account_id}")

    async def stop_all(self):
        logger.info("Stopping all traders...")
        account_ids = list(self._tasks.keys())
        for aid in account_ids:
            await self.stop_account(aid)
        # Stop WebSocket kline manager
        await self._kline_ws.stop()

    async def reload_account(self, account_id: UUID):
        await self.stop_account(account_id)
        await self.start_account(account_id)

    async def resume_buying(self, account_id: UUID):
        """Resume buying for a paused account and wake the trading loop."""
        from app.services.buy_pause_manager import BuyPauseManager
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
