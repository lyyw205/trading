from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime
from uuid import UUID
from typing import TYPE_CHECKING

import contextvars
from app.db.session import TradingSessionLocal
from app.db.lot_repo import LotRepository
from app.db.order_repo import OrderRepository
from app.db.position_repo import PositionRepository
from app.db.account_repo import AccountRepository
from app.exchange.binance_client import BinanceClient
from app.strategies.registry import StrategyRegistry
from app.strategies.state_store import StrategyStateStore
from app.strategies.base import BaseStrategy, StrategyContext, RepositoryBundle
from app.services.account_state_manager import AccountStateManager
from app.utils.logging import current_account_id

if TYPE_CHECKING:
    from app.services.price_collector import PriceCollector
    from app.services.rate_limiter import GlobalRateLimiter
    from app.utils.encryption import EncryptionManager

logger = logging.getLogger(__name__)


class AccountTrader:
    """
    단일 계정 매매 루프.
    - Strategy instances cached per account lifetime
    - StrategyStateStore + AccountStateManager for state
    - Circuit breaker: 5 consecutive failures -> auto-disable
    - Exponential backoff: 1s, 2s, 4s, 8s, max 60s
    """

    def __init__(
        self,
        account_id: UUID,
        price_collector: "PriceCollector",
        rate_limiter: "GlobalRateLimiter",
        encryption: "EncryptionManager",
    ):
        self.account_id = account_id
        self._running = True
        self._client: BinanceClient | None = None
        self._strategy_instances: dict[str, BaseStrategy] = {}
        self._price_collector = price_collector
        self._rate_limiter = rate_limiter
        self._encryption = encryption
        self._consecutive_failures = 0
        self._last_success_at: float | None = None

    async def _init_client(self):
        """Initialize the BinanceClient with decrypted API keys"""
        async with TradingSessionLocal() as session:
            repo = AccountRepository(session)
            account = await repo.get_by_id(self.account_id)
            if not account:
                raise RuntimeError(f"Account {self.account_id} not found")
            api_key = self._encryption.decrypt(account.api_key_encrypted)
            api_secret = self._encryption.decrypt(account.api_secret_encrypted)
            self._client = BinanceClient(api_key, api_secret, account.symbol)
            self._price_collector.register_client(account.symbol, self._client)

    def _get_or_create_strategy(self, name: str) -> BaseStrategy:
        if name not in self._strategy_instances:
            self._strategy_instances[name] = StrategyRegistry.create_instance(name)
        return self._strategy_instances[name]

    async def step(self):
        """Single trading cycle (corresponds to btc_trader.py step())"""
        # Set account context for structured logging
        token = current_account_id.set(str(self.account_id))
        try:
            async with TradingSessionLocal() as session:
                # Load account
                account_repo = AccountRepository(session)
                account = await account_repo.get_by_id(self.account_id)
                if not account or not account.is_active:
                    return

                # Rate limiter
                await self._rate_limiter.acquire(weight=1)

                # Sync orders and fills
                order_repo = OrderRepository(session)
                position_repo = PositionRepository(session)
                await self._sync_orders_and_fills(account, order_repo, position_repo)

                # Current price (via PriceCollector)
                cur_price = await self._price_collector.get_price(account.symbol)
                if cur_price <= 0:
                    cur_price = await self._price_collector.refresh_symbol(account.symbol)
                if cur_price <= 0:
                    logger.warning(f"[{self.account_id}] Price is 0, skipping cycle")
                    return

                # Store snapshots
                await self._price_collector.maybe_store_snapshot(account.symbol, cur_price, session)
                await self._price_collector.maybe_store_candle(account.symbol, cur_price, session)

                # Run active strategies
                lot_repo = LotRepository(session)
                repos = RepositoryBundle(
                    lot=lot_repo,
                    order=order_repo,
                    position=position_repo,
                    price=None,  # price_repo is module-level functions
                )

                # Load strategy configs for this account
                from app.models.strategy_config import StrategyConfig
                from sqlalchemy import select
                stmt = select(StrategyConfig).where(
                    StrategyConfig.account_id == self.account_id,
                    StrategyConfig.is_enabled == True,
                )
                result = await session.execute(stmt)
                strategy_configs = list(result.scalars().all())

                for sc in strategy_configs:
                    strategy = self._get_or_create_strategy(sc.strategy_name)
                    ctx = StrategyContext(
                        account_id=self.account_id,
                        symbol=account.symbol,
                        base_asset=account.base_asset,
                        quote_asset=account.quote_asset,
                        current_price=cur_price,
                        params=strategy.validate_params(sc.params or {}),
                        client_order_prefix=f"CMT_{str(self.account_id)[:8]}_",
                    )
                    state = StrategyStateStore(self.account_id, sc.strategy_name, session)
                    account_state = AccountStateManager(self.account_id, session)
                    await strategy.tick(ctx, state, self._client, account_state, repos)

                # Record success
                self._consecutive_failures = 0
                self._last_success_at = time.time()
                await account_repo.update_last_success(self.account_id)
                await session.commit()
        finally:
            current_account_id.reset(token)

    async def _sync_orders_and_fills(self, account, order_repo: OrderRepository, position_repo: PositionRepository):
        """Sync open orders and recent fills from exchange"""
        # Get recent open orders from DB
        open_ids = await order_repo.get_recent_open_orders(self.account_id)

        # Get open orders from exchange
        try:
            await self._rate_limiter.acquire(weight=3)  # get_open_orders is weight 3
            ex_open = await self._client.get_open_orders(account.symbol)
            for o in ex_open:
                await order_repo.upsert_order(self.account_id, o)
                oid = int(o["orderId"])
                if oid not in open_ids:
                    open_ids.append(oid)
        except Exception as e:
            logger.warning(f"[{self.account_id}] Open orders sync failed: {e}")

        # Refresh each tracked order
        for oid in open_ids[:50]:
            try:
                await self._rate_limiter.acquire(weight=1)
                o = await self._client.get_order(oid, account.symbol)
                await order_repo.upsert_order(self.account_id, o)
            except Exception as e:
                logger.warning(f"[{self.account_id}] Order {oid} sync failed: {e}")

        # Sync recent fills
        try:
            await self._rate_limiter.acquire(weight=5)  # get_my_trades weight
            trades = await self._client.get_my_trades(account.symbol)
            seen_oids = set()
            for t in trades:
                oid = int(t.get("orderId", 0))
                if oid > 0 and oid not in seen_oids:
                    seen_oids.add(oid)
                    try:
                        await self._rate_limiter.acquire(weight=1)
                        o = await self._client.get_order(oid, account.symbol)
                        await order_repo.upsert_order(self.account_id, o)
                    except Exception:
                        pass
                await order_repo.insert_fill(self.account_id, oid, t)
        except Exception as e:
            logger.warning(f"[{self.account_id}] Fills sync failed: {e}")

        # Recompute position
        await position_repo.recompute_from_fills(self.account_id, account.symbol)

    async def run_forever(self):
        """Main trading loop with circuit breaker and exponential backoff"""
        await self._init_client()
        logger.info(f"[{self.account_id}] Trading loop started")

        while self._running:
            try:
                await self.step()
            except Exception as e:
                self._consecutive_failures += 1
                logger.error(f"[{self.account_id}] Loop error ({self._consecutive_failures}x): {e}")

                if self._consecutive_failures >= 5:
                    await self._disable_with_circuit_breaker()
                    return

                backoff = min(60, 2 ** (self._consecutive_failures - 1))
                await asyncio.sleep(backoff)
                continue

            # Normal interval
            interval = await self._get_loop_interval()
            await asyncio.sleep(interval)

    async def _get_loop_interval(self) -> int:
        async with TradingSessionLocal() as session:
            repo = AccountRepository(session)
            account = await repo.get_by_id(self.account_id)
            return account.loop_interval_sec if account else 60

    async def _disable_with_circuit_breaker(self):
        async with TradingSessionLocal() as session:
            repo = AccountRepository(session)
            await repo.update_circuit_breaker(
                self.account_id,
                failures=self._consecutive_failures,
                disabled_at=datetime.utcnow(),
            )
            await session.commit()
        logger.critical(f"[{self.account_id}] Circuit breaker triggered: {self._consecutive_failures} consecutive failures")
        self._running = False

    def stop(self):
        self._running = False

    def health_status(self) -> dict:
        return {
            "running": self._running,
            "consecutive_failures": self._consecutive_failures,
            "last_success_at": self._last_success_at,
        }
