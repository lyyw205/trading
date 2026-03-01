from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.db.account_repo import AccountRepository
from app.db.lot_repo import LotRepository
from app.db.order_repo import OrderRepository
from app.db.position_repo import PositionRepository
from app.db.session import TradingSessionLocal
from app.exchange.binance_client import BinanceClient
from app.models.account import BuyPauseState
from app.services.account_state_manager import AccountStateManager
from app.services.buy_pause_manager import MIN_TRADE_USDT, BuyPauseManager
from app.strategies.base import BaseBuyLogic, BaseSellLogic, RepositoryBundle, StrategyContext
from app.strategies.registry import BuyLogicRegistry, SellLogicRegistry
from app.strategies.state_store import StrategyStateStore
from app.utils.logging import current_account_id, current_cycle_id

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
    - Buy pause: 잔고 부족 시 매수만 일시정지, 매도 계속
    """

    def __init__(
        self,
        account_id: UUID,
        price_collector: PriceCollector,
        rate_limiter: GlobalRateLimiter,
        encryption: EncryptionManager,
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
        # Buy pause state (in-memory, synced from DB each step)
        self._buy_pause_state: str = BuyPauseState.ACTIVE
        self._consecutive_low_balance: int = 0
        self._has_open_positions: bool = False
        self._buy_pause_mgr: BuyPauseManager | None = None
        self._throttle_cycle: int = 0
        # Wake event for interruptible sleep (manual resume)
        self._wake_event = asyncio.Event()

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
        # Generate cycle ID for correlation across logs
        cycle_id = uuid4().hex[:12]
        cycle_token = current_cycle_id.set(cycle_id)
        # Set account context for structured logging
        token = current_account_id.set(str(self.account_id))
        try:
            async with TradingSessionLocal() as session:
                # Load account
                account_repo = AccountRepository(session)
                account = await account_repo.get_by_id(self.account_id)
                if not account or not account.is_active:
                    return

                # Sync buy-pause state from DB
                self._buy_pause_state = account.buy_pause_state or BuyPauseState.ACTIVE
                self._consecutive_low_balance = account.consecutive_low_balance or 0

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

                # --- Balance pre-check (account-level, single API call) ---
                balance_ok = True
                try:
                    free_balance = await self._client.get_free_balance(account.quote_asset)
                    balance_ok = free_balance >= MIN_TRADE_USDT
                except Exception as e:
                    logger.warning("[%s] Balance check failed: %s, skipping buy evaluation", self.account_id, e)
                    # 잔고 API 실패 → 상태 변경 없이 이번 사이클 매수 스킵
                    balance_ok = False

                # Run active strategies
                lot_repo = LotRepository(session)
                repos = RepositoryBundle(
                    lot=lot_repo,
                    order=order_repo,
                    position=position_repo,
                    price=None,  # price_repo is module-level functions
                )

                from sqlalchemy import func, select

                from app.models.lot import Lot

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

                # Sentry context for this trading cycle
                try:
                    import sentry_sdk
                    sentry_sdk.set_tag("account_id", str(self.account_id))
                    sentry_sdk.set_tag("trading_cycle", cycle_id)
                    sentry_sdk.set_context("trading", {
                        "buy_pause_state": str(self._buy_pause_state),
                        "active_combos": len(combos),
                    })
                except Exception:
                    pass  # Sentry not configured

                # --- Open lots snapshot (for sell detection) ---
                open_lots_before_stmt = select(func.count()).select_from(Lot).where(
                    Lot.account_id == self.account_id, Lot.status == "OPEN",
                )
                open_lots_before = (await session.execute(open_lots_before_stmt)).scalar_one()

                # Buy pause manager (shares step session)
                pause_mgr = BuyPauseManager(self.account_id, session)
                self._buy_pause_mgr = pause_mgr

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

                        # 0. pre_tick: recenter (항상 실행 — PAUSED에서도 base_price 유지)
                        await buy_logic.pre_tick(buy_ctx, combo_state, self._client, repos, combo.id)

                        # 1. 매도 (항상 실행 — 기존 로트 관리, TP 체결 시 적립)
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

                        # 2. 매수 (buy-pause 가드 적용)
                        should_buy, self._throttle_cycle = BuyPauseManager.should_attempt_buy(
                            self._buy_pause_state, balance_ok, self._throttle_cycle,
                        )
                        if should_buy:
                            await buy_logic.tick(buy_ctx, combo_state, self._client, account_state, repos, combo.id)

                # --- Sell detection: 로트 수 비교 ---
                open_lots_after = (await session.execute(open_lots_before_stmt)).scalar_one()
                sell_occurred = open_lots_after < open_lots_before
                self._has_open_positions = open_lots_after > 0

                # 매도 발생 + PAUSED → 잔고 재체크
                if sell_occurred and self._buy_pause_state == BuyPauseState.PAUSED:
                    try:
                        fresh_balance = await self._client.get_free_balance(account.quote_asset)
                        balance_ok = fresh_balance >= MIN_TRADE_USDT
                        if balance_ok:
                            logger.info("[%s] Sell detected + balance recovered → will resume", self.account_id)
                    except Exception:
                        pass  # 재체크 실패 시 기존 balance_ok 유지

                # --- Buy pause state transition ---
                new_state, new_count = await pause_mgr.update_state(
                    self._buy_pause_state, self._consecutive_low_balance,
                    balance_ok, sell_occurred,
                )
                self._buy_pause_state = new_state
                self._consecutive_low_balance = new_count

                # Record success
                self._consecutive_failures = 0
                self._last_success_at = time.time()
                await account_repo.update_last_success(self.account_id)
                await session.commit()
        finally:
            current_account_id.reset(token)
            current_cycle_id.reset(cycle_token)

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
            except TimeoutError:
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

            # Dynamic interval (buy-pause aware)
            base_interval = await self._get_loop_interval()
            interval = BuyPauseManager.compute_interval(
                base_interval, self._buy_pause_state, self._has_open_positions,
            )
            await self._interruptible_sleep(interval)

    async def _interruptible_sleep(self, seconds: float):
        """Sleep that can be interrupted by _wake_event (manual resume)."""
        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=seconds)
        except TimeoutError:
            pass

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

    def wake(self):
        """Wake the trading loop from interruptible sleep (for manual resume)."""
        self._wake_event.set()

    def health_status(self) -> dict:
        return {
            "running": self._running,
            "consecutive_failures": self._consecutive_failures,
            "last_success_at": self._last_success_at,
            "buy_pause_state": self._buy_pause_state,
        }
