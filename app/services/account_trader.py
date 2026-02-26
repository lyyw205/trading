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
from app.strategies.registry import BuyLogicRegistry, SellLogicRegistry
from app.strategies.state_store import StrategyStateStore
from app.strategies.base import BaseBuyLogic, BaseSellLogic, StrategyContext, RepositoryBundle
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
        self._buy_instances: dict[UUID, BaseBuyLogic] = {}
        self._sell_instances: dict[UUID, BaseSellLogic] = {}
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
            # Phase 3-C: DB에서 서킷 브레이커 상태 복원
            self._consecutive_failures = account.circuit_breaker_failures or 0
            if self._consecutive_failures >= 5:
                logger.warning(f"[{self.account_id}] Circuit breaker already tripped ({self._consecutive_failures} failures), not starting")
                raise RuntimeError(f"Circuit breaker active: {self._consecutive_failures} failures")
            api_key = self._encryption.decrypt(account.api_key_encrypted)
            api_secret = self._encryption.decrypt(account.api_secret_encrypted)
            self._client = BinanceClient(api_key, api_secret, account.symbol)
            self._price_collector.register_client(account.symbol, self._client)

    def _get_or_create_buy(self, combo_id: UUID, name: str) -> BaseBuyLogic:
        if combo_id not in self._buy_instances:
            self._buy_instances[combo_id] = BuyLogicRegistry.create_instance(name)
        return self._buy_instances[combo_id]

    def _get_or_create_sell(self, combo_id: UUID, name: str) -> BaseSellLogic:
        if combo_id not in self._sell_instances:
            self._sell_instances[combo_id] = SellLogicRegistry.create_instance(name)
        return self._sell_instances[combo_id]

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

                from sqlalchemy import select

                # --- Combo-based execution (Phase 3) ---
                from app.models.trading_combo import TradingCombo
                combo_stmt = select(TradingCombo).where(
                    TradingCombo.account_id == self.account_id,
                    TradingCombo.is_enabled == True,
                )
                combo_result = await session.execute(combo_stmt)
                combos = list(combo_result.scalars().all())

                if not combos:
                    logger.debug("[%s] No active combos, skipping cycle", self.account_id)
                    return

                for combo in combos:
                        buy_logic = self._get_or_create_buy(combo.id, combo.buy_logic_name)
                        sell_logic = self._get_or_create_sell(combo.id, combo.sell_logic_name)

                        combo_state = StrategyStateStore(self.account_id, str(combo.id), session)
                        account_state = AccountStateManager(self.account_id, session)
                        prefix = f"CMT_{str(self.account_id)[:8]}_{str(combo.id)[:8]}_"

                        # Buy params (inject reference_combo_id if set)
                        buy_params = buy_logic.validate_params(combo.buy_params or {})
                        if combo.reference_combo_id:
                            buy_params["_reference_combo_id"] = str(combo.reference_combo_id)

                        buy_ctx = StrategyContext(
                            account_id=self.account_id,
                            symbol=account.symbol,
                            base_asset=account.base_asset,
                            quote_asset=account.quote_asset,
                            current_price=cur_price,
                            params=buy_params,
                            client_order_prefix=prefix,
                        )

                        # 0. pre_tick: recenter (base_price 순서 보호)
                        await buy_logic.pre_tick(buy_ctx, combo_state, self._client, repos, combo.id)

                        # 1. 매도 (기존 로트 관리, TP 체결 시 core_bucket 적립)
                        sell_params = sell_logic.validate_params(combo.sell_params or {})
                        sell_ctx = StrategyContext(
                            account_id=self.account_id,
                            symbol=account.symbol,
                            base_asset=account.base_asset,
                            quote_asset=account.quote_asset,
                            current_price=cur_price,
                            params=sell_params,
                            client_order_prefix=prefix,
                        )
                        open_lots = await lot_repo.get_open_lots_by_combo(
                            self.account_id, account.symbol, combo.id,
                        )
                        await sell_logic.tick(sell_ctx, combo_state, self._client, account_state, repos, open_lots)

                        # 2. 매수 (pending 처리 + 신규 매수)
                        await buy_logic.tick(buy_ctx, combo_state, self._client, account_state, repos, combo.id)

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
        # Phase 3-A: _init_client() 실패 시 서킷 브레이커 발동
        try:
            await self._init_client()
        except Exception as e:
            logger.error(f"[{self.account_id}] _init_client() failed: {e}")
            self._consecutive_failures = 5
            await self._disable_with_circuit_breaker()
            return

        logger.info(f"[{self.account_id}] Trading loop started")

        while self._running:
            try:
                # Phase 3-B: step() 타임아웃 (180초)
                await asyncio.wait_for(self.step(), timeout=180)
            except asyncio.TimeoutError:
                self._consecutive_failures += 1
                logger.error(f"[{self.account_id}] step() timed out (180s), failures: {self._consecutive_failures}")

                if self._consecutive_failures >= 5:
                    await self._disable_with_circuit_breaker()
                    return

                backoff = min(60, 2 ** (self._consecutive_failures - 1))
                await asyncio.sleep(backoff)
                continue
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
