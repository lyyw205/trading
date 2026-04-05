from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db.account_repo import AccountRepository
from app.db.lot_repo import LotRepository
from app.db.order_repo import OrderRepository
from app.db.position_repo import PositionRepository
from app.db.session import TradingSessionLocal
from app.exchange.binance_client import BinanceClient
from app.models.account import BuyPauseState
from app.models.fill import Fill
from app.models.lot import Lot
from app.models.order import Order
from app.models.trading_combo import TradingCombo
from app.services.account_state_manager import AccountStateManager
from app.services.alert_service import get_alert_service
from app.services.buy_pause_manager import MIN_TRADE_USDT, BuyPauseManager
from app.strategies.base import BaseBuyLogic, BaseSellLogic, RepositoryBundle, StrategyContext
from app.strategies.registry import BuyLogicRegistry, SellLogicRegistry
from app.strategies.state_store import StrategyStateStore
from app.utils.error_classification import ErrorType, classify_error
from app.utils.logging import current_account_id, current_cycle_id
from app.utils.metrics import CIRCUIT_BREAKER_TRIPS, TRADING_CYCLE_DURATION
from app.utils.symbol_parser import parse_symbol

if TYPE_CHECKING:
    from app.services.price_collector import PriceCollector
    from app.services.rate_limiter import GlobalRateLimiter
    from app.utils.encryption import EncryptionManager

logger = logging.getLogger(__name__)

# 서킷 브레이커 발동 임계값 (연속 실패 횟수)
CB_FAILURE_THRESHOLD = 5


class AccountTrader:
    """
    단일 계정 매매 루프.
    - Strategy instances cached per account lifetime
    - StrategyStateStore + AccountStateManager for state
    - Circuit breaker: PERMANENT 에러(API키 무효 등)만 계정 차단
    - Transient/timeout 에러: 매수 일시정지 + 백오프, 매도 계속
    - Buy pause: 잔고 부족 시 매수만 일시정지, 매도 계속
    """

    def __init__(
        self,
        account_id: UUID,
        price_collector: PriceCollector,
        rate_limiter: GlobalRateLimiter,
        encryption: EncryptionManager,
        *,
        initial_symbols: set[str] | None = None,
    ):
        self.account_id = account_id
        self._running = True
        self._client: BinanceClient | None = None
        self._is_paper: bool = False
        self._buy_instances: dict[tuple[UUID, str], BaseBuyLogic] = {}
        self._sell_instances: dict[tuple[UUID, str], BaseSellLogic] = {}
        self._price_collector = price_collector
        self._rate_limiter = rate_limiter
        self._encryption = encryption
        self._initial_symbols: set[str] = initial_symbols or set()
        self._consecutive_failures = 0
        self._failure_history: list[str] = []  # CB 발동 시 실패 사유 포함용
        self._last_success_at: float | None = None
        # Buy pause state (in-memory, synced from DB each step)
        self._buy_pause_state: str = BuyPauseState.ACTIVE
        self._consecutive_low_balance: int = 0
        self._has_open_positions: bool = False
        self._buy_pause_mgr: BuyPauseManager | None = None
        self._throttle_cycle: int = 0
        self._last_scan_log_at: float = 0.0  # 스캔 로그 throttle (1시간 간격)
        # Wake event for interruptible sleep (manual resume)
        self._wake_event = asyncio.Event()

    async def _init_client(self):
        """Initialize the exchange client (BinanceClient or BacktestClient for paper accounts)."""
        async with TradingSessionLocal() as session:
            repo = AccountRepository(session)
            account = await repo.get_by_id(self.account_id)
            if not account:
                raise RuntimeError(f"Account {self.account_id} not found")
            # Phase 3-C: DB에서 서킷 브레이커 상태 복원
            self._consecutive_failures = account.circuit_breaker_failures or 0
            if self._consecutive_failures >= CB_FAILURE_THRESHOLD:
                logger.warning(
                    "Circuit breaker already tripped (%d failures), not starting", self._consecutive_failures
                )
                raise RuntimeError(f"Circuit breaker active: {self._consecutive_failures} failures")

            if account.is_paper:
                from app.exchange.backtest_client import BacktestClient

                # 페이퍼 계정: DB에서 잔고 복원 후 BacktestClient 생성
                balance = await self._restore_paper_balance(session, account)
                self._client = BacktestClient(
                    symbol=account.symbol,
                    initial_balance_usdt=balance["usdt_free"],
                )
                # 보유 코인 잔고 복원 (open lots 기반)
                for asset, qty in balance["assets"].items():
                    if asset not in self._client._balances:
                        self._client._balances[asset] = {"free": 0.0, "locked": 0.0}
                    self._client._balances[asset]["free"] = qty
                self._is_paper = True
                logger.info("페이퍼 계정 초기화 완료: USDT=%.2f, assets=%s", balance["usdt_free"], balance["assets"])
            else:
                api_key = self._encryption.decrypt(account.api_key_encrypted)
                api_secret = self._encryption.decrypt(account.api_secret_encrypted)
                self._client = BinanceClient(api_key, api_secret, account.symbol)
                self._is_paper = False

            # Register client for pre-passed combo symbols (avoids redundant DB query)
            all_symbols = {account.symbol}
            all_symbols.update(s.upper() for s in self._initial_symbols)
            for symbol in all_symbols:
                self._price_collector.register_client(symbol, self._client)

    async def _restore_paper_balance(self, session, account) -> dict:
        """페이퍼 계정 잔고를 DB fills 기반으로 복원.

        Formula: current_usdt = initial_balance - Σ(buy_cost) + Σ(sell_revenue)
        Asset balances computed from open lots.
        """
        initial = float(account.paper_initial_balance)

        # 매수 총액 (quote_qty 합산)
        buy_sum_stmt = select(func.coalesce(func.sum(Fill.quote_qty), 0)).where(
            Fill.account_id == self.account_id,
            Fill.side == "BUY",
        )
        buy_total = float((await session.execute(buy_sum_stmt)).scalar_one())

        # 매도 총액 (quote_qty 합산)
        sell_sum_stmt = select(func.coalesce(func.sum(Fill.quote_qty), 0)).where(
            Fill.account_id == self.account_id,
            Fill.side == "SELL",
        )
        sell_total = float((await session.execute(sell_sum_stmt)).scalar_one())

        usdt_free = initial - buy_total + sell_total

        # Open lots 기반 코인 잔고 복원
        open_lots_stmt = (
            select(Lot.symbol, func.sum(Lot.buy_qty))
            .where(
                Lot.account_id == self.account_id,
                Lot.status == "OPEN",
            )
            .group_by(Lot.symbol)
        )
        lot_result = await session.execute(open_lots_stmt)
        assets: dict[str, float] = {}
        for symbol, qty in lot_result.all():
            base_asset, _ = parse_symbol(symbol)
            assets[base_asset] = assets.get(base_asset, 0.0) + float(qty)

        logger.info(
            "페이퍼 잔고 복원: USDT=%.2f (초기=%.2f, 매수=%.2f, 매도=%.2f), assets=%s",
            usdt_free,
            initial,
            buy_total,
            sell_total,
            assets,
        )
        return {"usdt_free": usdt_free, "assets": assets}

    def _get_or_create_buy(self, combo_id: UUID, symbol: str, name: str) -> BaseBuyLogic:
        key = (combo_id, symbol)
        if key not in self._buy_instances:
            self._buy_instances[key] = BuyLogicRegistry.create_instance(name)
        return self._buy_instances[key]

    def _get_or_create_sell(self, combo_id: UUID, symbol: str, name: str) -> BaseSellLogic:
        key = (combo_id, symbol)
        if key not in self._sell_instances:
            self._sell_instances[key] = SellLogicRegistry.create_instance(name)
        return self._sell_instances[key]

    def _instrument_sentry(self, cycle_id: str, combos_count: int) -> None:
        """Set Sentry tags/context for the current cycle. Non-critical."""
        try:
            import sentry_sdk

            sentry_sdk.set_tag("account_id", str(self.account_id))
            sentry_sdk.set_tag("trading_cycle", cycle_id)
            sentry_sdk.set_context(
                "trading",
                {
                    "buy_pause_state": str(self._buy_pause_state),
                    "active_combos": combos_count,
                },
            )
        except ImportError:
            pass  # Sentry SDK not installed
        except Exception as e:
            logger.debug("Sentry instrumentation failed: %s", e)

    async def _run_combo_loop(
        self,
        combos: list,
        account,
        free_balance: float,
        is_balance_sufficient: bool,
        should_buy: bool,
        repos: RepositoryBundle,
        session,
        account_state: AccountStateManager,
        prefetched_lots: dict[tuple, list],
    ) -> None:
        """Execute scan logging and combo x symbol tick loop."""
        now = time.time()
        if now - self._last_scan_log_at >= 3600:
            total_symbols = sum(len(c.symbols) if c.symbols else 1 for c in combos)
            if self._buy_pause_state == BuyPauseState.PAUSED:
                logger.info(
                    "매도 감시 실행완료 | %d개 콤보, %d개 심볼 | 잔고=%.2f | 포지션=%s",
                    len(combos),
                    total_symbols,
                    free_balance,
                    "있음" if self._has_open_positions else "없음",
                )
            else:
                logger.info(
                    "스캔 중: %d개 콤보, %d개 심볼 | 잔고=%.2f | 상태=%s",
                    len(combos),
                    total_symbols,
                    free_balance,
                    self._buy_pause_state.value,
                )
            self._last_scan_log_at = now

        for combo in combos:
            # TODO: migrate to TradingCombo.symbols (legacy account.symbol fallback)
            combo_symbols = combo.symbols if combo.symbols else [account.symbol]

            for symbol in combo_symbols:
                await self._execute_symbol_tick(
                    combo,
                    symbol,
                    free_balance,
                    is_balance_sufficient,
                    should_buy,
                    repos,
                    session,
                    account_state,
                    prefetched_lots,
                )

    async def _post_cycle_sell_check(
        self,
        session,
        account,
        open_lots_count_before: int,
        is_balance_sufficient: bool,
        pause_mgr: BuyPauseManager,
    ) -> bool:
        """Detect sells, recheck balance if needed, update buy-pause state.

        Returns updated is_balance_sufficient.

        Side effects:
            - Sets self._has_open_positions based on open lot count
            - Updates self._buy_pause_state and self._consecutive_low_balance via pause_mgr
        """
        # --- Sell detection: compare open lot count before/after strategies ---
        open_lots_after_stmt = (
            select(func.count())
            .select_from(Lot)
            .where(
                Lot.account_id == self.account_id,
                Lot.status == "OPEN",
            )
        )
        open_lots_after = (await session.execute(open_lots_after_stmt)).scalar_one()
        did_sell_occur = open_lots_after < open_lots_count_before
        self._has_open_positions = open_lots_after > 0

        # 매도 발생 + PAUSED → 잔고 재체크
        if did_sell_occur and self._buy_pause_state == BuyPauseState.PAUSED:
            try:
                fresh_balance = await self._client.get_free_balance(account.quote_asset)
                is_balance_sufficient = fresh_balance >= MIN_TRADE_USDT
                if is_balance_sufficient:
                    logger.info("Sell detected + balance recovered → will resume")
            except Exception:
                pass  # 재체크 실패 시 기존 is_balance_sufficient 유지

        # --- Buy pause state transition ---
        prev_state = self._buy_pause_state
        new_state, new_count = await pause_mgr.update_state(
            self._buy_pause_state,
            self._consecutive_low_balance,
            is_balance_sufficient,
            did_sell_occur,
        )
        self._buy_pause_state = new_state
        self._consecutive_low_balance = new_count

        # 상태 전환 시 유저에게 보이는 로그
        if new_state != prev_state:
            if new_state == BuyPauseState.PAUSED:
                logger.info("잔고 부족으로 매수 일시중단. 매도 감시는 계속됩니다.")
            elif new_state == BuyPauseState.THROTTLED:
                logger.info("잔고 부족 감지. 매수 빈도를 줄여 운영합니다.")
            elif new_state == BuyPauseState.ACTIVE and prev_state != BuyPauseState.ACTIVE:
                logger.info("잔고가 회복되어 정상 매수를 재개합니다.")

        return is_balance_sufficient

    async def step(self) -> int:
        """Single trading cycle with DB retry. Returns loop_interval_sec."""
        last_exc: OperationalError | None = None
        for attempt in range(3):
            try:
                return await self._do_step()
            except OperationalError as e:
                last_exc = e
                if attempt < 2:
                    logger.warning("DB connection error (attempt %d/3): %s", attempt + 1, e)
                    await asyncio.sleep(2**attempt)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"AccountTrader {self.account_id}: step failed after retries with no captured exception")

    async def _do_step(self) -> int:
        """Inner step logic. Returns loop_interval_sec for run_forever."""
        start_time = time.perf_counter()
        result, cycle_id = 60, uuid4().hex[:12]
        cycle_token = current_cycle_id.set(cycle_id)
        token = current_account_id.set(str(self.account_id))
        try:
            async with TradingSessionLocal() as session:
                account_repo = AccountRepository(session)
                account = await account_repo.get_by_id(self.account_id)
                if not account or not account.is_active:
                    return 60
                raw_state = account.buy_pause_state
                self._buy_pause_state = BuyPauseState(raw_state) if raw_state else BuyPauseState.ACTIVE
                self._consecutive_low_balance = account.consecutive_low_balance or 0
                await self._rate_limiter.acquire(weight=1)
                order_repo, position_repo = OrderRepository(session), PositionRepository(session)
                is_balance_sufficient, free_balance = True, 0.0
                try:
                    free_balance = await self._client.get_free_balance(account.quote_asset)
                    is_balance_sufficient = free_balance >= MIN_TRADE_USDT
                except Exception as e:
                    logger.warning("Balance check failed: %s, skipping buy evaluation", e)
                    is_balance_sufficient = False
                lot_repo = LotRepository(session)
                repos = RepositoryBundle(lot=lot_repo, order=order_repo, position=position_repo, price=None)
                combo_stmt = select(TradingCombo).where(
                    TradingCombo.account_id == self.account_id,
                    TradingCombo.is_enabled.is_(True),
                )
                combos = list((await session.execute(combo_stmt)).scalars().all())
                if not combos:
                    return result
                # Sync orders/fills and reconcile orphans
                all_combo_symbols = {account.symbol}
                for c in combos:
                    if c.symbols:
                        all_combo_symbols.update(s.upper() for s in c.symbols)
                await self._sync_orders_and_fills(account, all_combo_symbols, order_repo, position_repo, session)
                orphan_count = 0
                try:
                    async with session.begin_nested():
                        orphan_count = await self._reconcile_orphan_sells(order_repo, lot_repo, session)
                except Exception as e:
                    logger.warning("Orphan reconciliation failed (non-fatal): %s", e)
                if orphan_count > 0:
                    logger.warning("Reconciled %d orphaned sell orders for account %s", orphan_count, self.account_id)
                self._instrument_sentry(cycle_id, len(combos))
                pause_mgr = BuyPauseManager(self.account_id, session)
                self._buy_pause_mgr = pause_mgr
                account_state = AccountStateManager(self.account_id, session)
                await account_state.preload()
                should_buy, self._throttle_cycle = BuyPauseManager.should_attempt_buy(
                    self._buy_pause_state,
                    is_balance_sufficient,
                    self._throttle_cycle,
                )
                all_open_lots = await repos.lot.get_all_open_lots_for_account(self.account_id)
                prefetched_lots: dict[tuple, list] = {}
                for lot in all_open_lots:
                    prefetched_lots.setdefault((lot.combo_id, lot.symbol), []).append(lot)
                await self._run_combo_loop(
                    combos,
                    account,
                    free_balance,
                    is_balance_sufficient,
                    should_buy,
                    repos,
                    session,
                    account_state,
                    prefetched_lots,
                )
                is_balance_sufficient = await self._post_cycle_sell_check(
                    session,
                    account,
                    len(all_open_lots),
                    is_balance_sufficient,
                    pause_mgr,
                )
                # Record success
                self._consecutive_failures = 0
                self._failure_history.clear()
                self._last_success_at = time.time()
                await account_repo.update_last_success(self.account_id)
                if account.auto_recovery_attempts and account.auto_recovery_attempts > 0:
                    await account_repo.reset_auto_recovery_on_success(account_id=self.account_id)
                await session.commit()
                result = account.loop_interval_sec if account.loop_interval_sec else 60
        finally:
            current_account_id.reset(token)
            current_cycle_id.reset(cycle_token)
            TRADING_CYCLE_DURATION.labels(account_id=str(self.account_id)).observe(time.perf_counter() - start_time)
        return result

    async def _execute_symbol_tick(
        self,
        combo: TradingCombo,
        symbol: str,
        free_balance: float,
        is_balance_sufficient: bool,
        should_buy: bool,
        repos: RepositoryBundle,
        session,
        account_state: AccountStateManager,
        prefetched_lots: dict[tuple, list] | None = None,
    ) -> None:
        """Execute buy/sell strategies for a single combo×symbol pair."""
        try:
            base_asset, quote_asset = parse_symbol(symbol)
        except ValueError:
            logger.warning("Cannot parse symbol %s, skipping", symbol)
            return

        # Fetch price for this symbol
        cur_price = await self._price_collector.get_price(symbol)
        if cur_price <= 0:
            cur_price = await self._price_collector.refresh_symbol(symbol)
        if cur_price <= 0:
            logger.warning("Price is 0 for %s, skipping", symbol)
            return

        # 페이퍼 계정: 라이브 가격을 BacktestClient에 주입 (주문 체결 시뮬레이션)
        if self._is_paper and hasattr(self._client, "set_price"):
            self._client.set_price(cur_price)

        buy_logic = self._get_or_create_buy(combo.id, symbol, combo.buy_logic_name)
        sell_logic = self._get_or_create_sell(combo.id, symbol, combo.sell_logic_name)

        combo_state = StrategyStateStore(self.account_id, f"{combo.id}:{symbol}", session)
        await combo_state.preload()
        prefix = f"CMT_{str(self.account_id)[:8]}_{str(combo.id)[:8]}_"

        # Use prefetched lots if available, otherwise fall back to DB query
        if prefetched_lots is not None:
            open_lots = prefetched_lots.get((combo.id, symbol), [])
        else:
            open_lots = await repos.lot.get_open_lots_by_combo(
                self.account_id,
                symbol,
                combo.id,
            )
        # Buy params (inject reference_combo_id if set)
        buy_params = buy_logic.validate_params(combo.buy_params or {})
        if combo.reference_combo_id:
            buy_params["_reference_combo_id"] = str(combo.reference_combo_id)

        buy_ctx = StrategyContext(
            account_id=self.account_id,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            current_price=cur_price,
            params=buy_params,
            client_order_prefix=prefix,
            free_balance=free_balance if is_balance_sufficient else 0.0,
            open_lots=open_lots,
        )

        # 0. pre_tick: recenter (항상 실행 — PAUSED에서도 base_price 유지)
        await buy_logic.pre_tick(buy_ctx, combo_state, self._client, repos, combo.id)

        # 1. 매도 (항상 실행 — 기존 로트 관리, TP 체결 시 적립)
        sell_params = sell_logic.validate_params(combo.sell_params or {})
        sell_ctx = StrategyContext(
            account_id=self.account_id,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            current_price=cur_price,
            params=sell_params,
            client_order_prefix=prefix,
            free_balance=free_balance if is_balance_sufficient else 0.0,
            open_lots=open_lots,
        )
        await sell_logic.tick(sell_ctx, combo_state, self._client, account_state, repos, open_lots)

        # 2. 매수 (buy-pause 가드 적용 — 사이클 단위 판정)
        if should_buy:
            await buy_logic.tick(buy_ctx, combo_state, self._client, account_state, repos, combo.id)

    async def _sync_orders_and_fills(
        self,
        account,
        symbols: set[str],
        order_repo: OrderRepository,
        position_repo: PositionRepository,
        session,
    ):
        """Sync open orders and recent fills for all active symbols.

        Optimization: tracks already-synced order IDs to avoid duplicate API calls.
        Uses correct symbol per order (H-1 fix) instead of account default symbol.
        """
        open_ids = await order_repo.get_recent_open_orders(self.account_id)
        synced_oids: set[int] = set()

        # Step 1: Sync open orders per symbol from exchange (parallel fetch)
        async def _fetch_open_orders(symbol: str):
            await self._rate_limiter.acquire(weight=3)
            return symbol, await self._client.get_open_orders(symbol)

        open_order_results = await asyncio.gather(
            *[_fetch_open_orders(symbol) for symbol in symbols],
            return_exceptions=True,
        )
        all_open_orders: list[dict] = []
        for symbol, result in zip(symbols, open_order_results, strict=False):
            if isinstance(result, Exception):
                logger.warning("Open orders sync failed for %s: %s", symbol, result)
                continue
            _, ex_open = result
            for o in ex_open:
                all_open_orders.append(o)
                oid = int(o["orderId"])
                synced_oids.add(oid)
                if oid not in open_ids:
                    open_ids.append(oid)
        if all_open_orders:
            await order_repo.upsert_orders_batch(self.account_id, all_open_orders)

        # Step 2: Refresh tracked orders NOT already synced (parallel, use correct symbol from DB)
        to_refresh = [oid for oid in open_ids[:50] if oid not in synced_oids]
        if to_refresh:
            order_sym_stmt = select(Order.order_id, Order.symbol).where(
                Order.account_id == self.account_id,
                Order.order_id.in_(to_refresh),
            )
            order_sym_result = await session.execute(order_sym_stmt)
            order_symbol_map = {row[0]: row[1] for row in order_sym_result.all()}

            async def _fetch_order_data(oid: int):
                symbol = order_symbol_map.get(oid, account.symbol)
                await self._rate_limiter.acquire(weight=1)
                return oid, await self._client.get_order(oid, symbol)

            # API calls in parallel, DB writes sequential (AsyncSession is not concurrency-safe)
            results = await asyncio.gather(
                *[_fetch_order_data(oid) for oid in to_refresh],
                return_exceptions=True,
            )
            for res in results:
                if isinstance(res, Exception):
                    logger.warning("Order sync fetch failed: %s", res)
                    continue
                oid, order_data = res
                try:
                    await order_repo.upsert_order(self.account_id, order_data)
                    synced_oids.add(oid)
                except Exception as e:
                    logger.warning("Order %s upsert failed: %s", oid, e)

        # Step 3: Sync recent fills per symbol
        if self._is_paper:
            # Paper accounts: create fills from FILLED order raw_json (no exchange API)
            symbols_with_new_fills = await self._sync_paper_fills(symbols, order_repo, session)
        else:
            symbols_with_new_fills = await self._sync_exchange_fills(symbols, order_repo, synced_oids, session)

        # 4-6: Conditional recompute — only for symbols with new fills
        for symbol in symbols_with_new_fills:
            await position_repo.recompute_from_fills(self.account_id, symbol)

    async def _sync_paper_fills(self, symbols: set[str], order_repo: OrderRepository, session) -> set[str]:
        """Paper accounts: create Fill records from FILLED orders' raw_json."""
        # Find FILLED orders without corresponding fills
        filled_stmt = select(Order).where(
            Order.account_id == self.account_id,
            Order.symbol.in_(symbols),
            Order.status == "FILLED",
        )
        filled_result = await session.execute(filled_stmt)
        filled_orders = list(filled_result.scalars().all())

        # Get existing fill order_ids to skip
        existing_stmt = (
            select(Fill.order_id).where(Fill.account_id == self.account_id, Fill.symbol.in_(symbols)).distinct()
        )
        existing_result = await session.execute(existing_stmt)
        existing_oids = {row[0] for row in existing_result.all()}

        symbols_with_new_fills: set[str] = set()
        for order in filled_orders:
            if order.order_id in existing_oids:
                continue
            raw = order.raw_json or {}
            fills = raw.get("fills", [])
            if not fills:
                continue
            fill_rows = []
            for i, f in enumerate(fills):
                is_buyer = (order.side or "").upper() == "BUY"
                trade_data = {
                    "id": order.order_id * 100 + i,  # synthetic trade_id
                    "orderId": order.order_id,
                    "symbol": order.symbol,
                    "isBuyer": is_buyer,
                    "price": f.get("price", "0"),
                    "qty": f.get("qty", "0"),
                    "quoteQty": str(float(f.get("price", 0)) * float(f.get("qty", 0))),
                    "commission": f.get("commission", "0"),
                    "commissionAsset": f.get("commissionAsset", "USDT"),
                    "time": order.update_time_ms or 0,
                }
                fill_rows.append((order.order_id, trade_data))
            if fill_rows:
                await order_repo.insert_fills_batch(self.account_id, fill_rows)
                symbols_with_new_fills.add(order.symbol)
                logger.info(
                    "Paper fill created: order %s symbol %s (%d fills)", order.order_id, order.symbol, len(fill_rows)
                )

        return symbols_with_new_fills

    async def _sync_exchange_fills(
        self, symbols: set[str], order_repo: OrderRepository, synced_oids: set[int], session
    ) -> set[str]:
        """Real accounts: sync fills from exchange API (incremental via last trade_id)."""
        try:
            max_id_stmt = (
                select(Fill.symbol, func.max(Fill.trade_id))
                .where(Fill.account_id == self.account_id, Fill.symbol.in_(symbols))
                .group_by(Fill.symbol)
            )
            max_id_result = await session.execute(max_id_stmt)
            last_trade_ids: dict[str, int] = {row[0]: row[1] for row in max_id_result.all()}
        except Exception:
            logger.warning("MAX(trade_id) lookup failed, falling back to full fetch")
            last_trade_ids = {}

        async def _fetch_trades(symbol: str):
            await self._rate_limiter.acquire(weight=5)
            last_id = last_trade_ids.get(symbol)
            if last_id is not None:
                return symbol, await self._client.get_my_trades_from_id(symbol, from_id=last_id + 1)
            return symbol, await self._client.get_my_trades(symbol)

        trade_results = await asyncio.gather(
            *[_fetch_trades(symbol) for symbol in symbols],
            return_exceptions=True,
        )

        symbols_with_new_fills: set[str] = set()
        for symbol, result in zip(symbols, trade_results, strict=False):
            if isinstance(result, Exception):
                logger.warning("Fills sync failed for %s: %s", symbol, result)
                continue
            _, trades = result
            try:
                seen_oids: set[int] = set()
                unseen_oids: list[int] = []
                fill_rows: list[tuple[int, dict]] = []
                for t in trades:
                    oid = int(t.get("orderId", 0))
                    if oid > 0 and oid not in seen_oids and oid not in synced_oids:
                        seen_oids.add(oid)
                        unseen_oids.append(oid)
                    fill_rows.append((oid, t))
                if fill_rows:
                    await order_repo.insert_fills_batch(self.account_id, fill_rows)

                if trades:
                    symbols_with_new_fills.add(symbol)

                # Parallel fetch for unseen order IDs (API parallel, DB sequential)
                if unseen_oids:

                    async def _fetch_fill_order_data(fill_oid: int, fill_sym: str = symbol):
                        await self._rate_limiter.acquire(weight=1)
                        return fill_oid, await self._client.get_order(fill_oid, fill_sym)

                    fill_results = await asyncio.gather(
                        *[_fetch_fill_order_data(oid) for oid in unseen_oids],
                        return_exceptions=True,
                    )
                    for res in fill_results:
                        if isinstance(res, Exception):
                            logger.warning("Fill order fetch failed: %s", res)
                            continue
                        fill_oid, order_data = res
                        try:
                            await order_repo.upsert_order(self.account_id, order_data)
                        except Exception as e:
                            logger.warning("Fill order %s upsert failed: %s", fill_oid, e)
            except Exception as e:
                logger.warning("Fills processing failed for %s: %s", symbol, e)

        return symbols_with_new_fills

    _ORPHAN_TP_RE = re.compile(r"^CMT_[0-9a-f]{8}_[0-9a-f]{8}__TP_(\d+)$")

    async def _reconcile_orphan_sells(
        self,
        order_repo: OrderRepository,
        lot_repo: LotRepository,
        session,
    ) -> int:
        """Reconcile orphaned sell orders: Binance has them, DB lots do not.

        Scans the Order table for SELL orders with clientOrderId matching
        the _TP_{lot_id} pattern, then links them to lots where sell_order_id IS NULL.

        Returns the number of orphans reconciled.
        """
        # 1. Find OPEN lots with sell_order_id IS NULL for this account
        orphan_lot_stmt = select(Lot.lot_id).where(
            Lot.account_id == self.account_id,
            Lot.status == "OPEN",
            Lot.sell_order_id.is_(None),
        )
        orphan_lot_result = await session.execute(orphan_lot_stmt)
        orphan_lot_ids = {row[0] for row in orphan_lot_result.all()}

        if not orphan_lot_ids:
            return 0

        # 2. Find open/new sell orders with clientOrderId containing _TP_
        sell_order_stmt = select(Order.order_id, Order.client_order_id, Order.update_time_ms).where(
            Order.account_id == self.account_id,
            Order.side == "SELL",
            Order.status.in_(("NEW", "PARTIALLY_FILLED")),
            Order.client_order_id.isnot(None),
        )
        sell_order_result = await session.execute(sell_order_stmt)

        reconciled = 0
        for order_id, client_order_id, update_time_ms in sell_order_result.all():
            match = self._ORPHAN_TP_RE.search(client_order_id or "")
            if not match:
                continue
            lot_id = int(match.group(1))
            if lot_id not in orphan_lot_ids:
                continue

            # Link the orphan
            await lot_repo.set_sell_order(
                account_id=self.account_id,
                lot_id=lot_id,
                sell_order_id=order_id,
                sell_order_time_ms=update_time_ms or 0,
            )
            orphan_lot_ids.discard(lot_id)  # prevent duplicate matching
            reconciled += 1
            logger.info(
                "Orphan recovery: linked sell order %s to lot %s (clientOrderId=%s)",
                order_id,
                lot_id,
                client_order_id,
            )

        if reconciled > 0:
            try:
                await session.flush()
            except Exception as flush_exc:
                logger.warning(
                    "Orphan recovery flush failed (%d links may be lost): %s",
                    reconciled,
                    flush_exc,
                )
                return 0

        return reconciled

    async def run_forever(self):
        """Main trading loop with circuit breaker and exponential backoff.

        Circuit breaker: PERMANENT 에러(API키 무효 등)만 계정 차단.
        Transient/timeout 에러: 매수만 일시정지, trader 루프는 계속 실행.
        """
        # Set account context early so _init_client and error logs include account_id
        token = current_account_id.set(str(self.account_id))
        try:
            # Phase 3-A: _init_client() 실패 시 — PERMANENT만 CB, 나머지는 재시도
            try:
                await self._init_client()
            except Exception as e:
                err_type = classify_error(e)
                logger.error("_init_client() failed (%s): %s", err_type.name, e)
                self._failure_history.append(f"init_client: {e}")
                if err_type == ErrorType.PERMANENT:
                    self._consecutive_failures = CB_FAILURE_THRESHOLD
                    await self._disable_with_circuit_breaker()
                    return
                # Transient init failure — pause buying, retry after backoff
                await self._pause_buying_on_error("init_client failed")
                await asyncio.sleep(30)
                # Retry init once more
                try:
                    await self._init_client()
                except Exception as e2:
                    logger.error("_init_client() retry failed: %s — CB trip", e2)
                    self._consecutive_failures = CB_FAILURE_THRESHOLD
                    await self._disable_with_circuit_breaker()
                    return

            logger.info("트레이딩 루프가 정상 시작되었습니다")

            while self._running:
                try:
                    # Phase 3-B: step() 타임아웃 (180초)
                    loop_interval = await asyncio.wait_for(self.step(), timeout=180)
                except TimeoutError:
                    self._consecutive_failures += 1
                    self._failure_history.append(f"[{self._consecutive_failures}] Timeout (180s)")
                    logger.error("step() timed out (180s), failures: %d", self._consecutive_failures)

                    # 매수만 일시정지, trader 루프는 계속
                    if self._consecutive_failures >= 3:
                        await self._pause_buying_on_error("consecutive timeouts")

                    backoff = min(60, 2 ** (self._consecutive_failures - 1))
                    await asyncio.sleep(backoff)
                    continue
                except Exception as e:
                    err_type = classify_error(e)
                    if err_type == ErrorType.PERMANENT:
                        logger.error("Permanent error, triggering CB: %s", e)
                        self._failure_history.append(f"[PERMANENT] {e}")
                        self._consecutive_failures = CB_FAILURE_THRESHOLD
                        await self._disable_with_circuit_breaker()
                        return
                    elif err_type == ErrorType.RATE_LIMIT:
                        logger.warning("Rate limited, backing off 120s: %s", e)
                        await asyncio.sleep(120)
                        continue
                    elif err_type == ErrorType.BALANCE:
                        logger.warning("Balance-related error → feeding back to BuyPauseManager: %s", e)
                        # 잔고 부족을 BuyPauseManager에 피드백 (THROTTLED → PAUSED 전환)
                        self._consecutive_low_balance += 1
                        if self._buy_pause_mgr:
                            new_state, new_count = await self._buy_pause_mgr.update_state(
                                self._buy_pause_state,
                                self._consecutive_low_balance,
                                False,
                                False,
                            )
                            if new_state != self._buy_pause_state:
                                logger.info("BuyPause: %s → %s (balance error)", self._buy_pause_state, new_state)
                                self._buy_pause_state = new_state
                                self._consecutive_low_balance = new_count
                        await asyncio.sleep(30)
                        continue
                    else:
                        # TRANSIENT: 매수 일시정지, trader는 계속 실행
                        self._consecutive_failures += 1
                        self._failure_history.append(f"[{self._consecutive_failures}] {err_type.name}: {e}")
                        logger.error("Transient error (%dx): %s", self._consecutive_failures, e)

                        if self._consecutive_failures >= 3:
                            await self._pause_buying_on_error(f"transient errors ({self._consecutive_failures}x)")

                    backoff = min(60, 2 ** (self._consecutive_failures - 1))
                    await asyncio.sleep(backoff)
                    continue

                # Success — reset failure counter and resume buying if paused by errors
                if self._consecutive_failures > 0:
                    logger.info("Step succeeded, resetting failure counter (was %d)", self._consecutive_failures)
                    self._consecutive_failures = 0
                    self._failure_history.clear()

                # Dynamic interval (buy-pause aware)
                base_interval = loop_interval if loop_interval else 60
                interval = BuyPauseManager.compute_interval(
                    base_interval,
                    self._buy_pause_state,
                    self._has_open_positions,
                )
                if self._buy_pause_state == BuyPauseState.PAUSED and not self._has_open_positions:
                    logger.info("포지션 없음. %.0f시간 후 재확인합니다.", interval / 3600)
                await self._interruptible_sleep(interval)
        finally:
            current_account_id.reset(token)

    async def _interruptible_sleep(self, seconds: float):
        """Sleep that can be interrupted by _wake_event (manual resume)."""
        self._wake_event.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake_event.wait(), timeout=seconds)

    async def _disable_with_circuit_breaker(self):
        async with TradingSessionLocal() as session:
            repo = AccountRepository(session)
            await repo.update_circuit_breaker(
                self.account_id,
                failures=self._consecutive_failures,
                disabled_at=datetime.now(UTC),
            )
            await session.commit()
        logger.critical("Circuit breaker triggered: %d consecutive failures", self._consecutive_failures)

        # Metrics
        CIRCUIT_BREAKER_TRIPS.labels(account_id=str(self.account_id)).inc()

        # Alert
        try:
            alert = get_alert_service()
            failure_detail = "\n".join(self._failure_history[-5:]) if self._failure_history else "N/A"
            await alert.send_critical(
                f"Circuit Breaker triggered\n"
                f"Account: {self.account_id}\n"
                f"Consecutive failures: {self._consecutive_failures}\n"
                f"Failure history:\n{failure_detail}\n"
                f"Auto recovery will attempt in 30 minutes"
            )
        except Exception as alert_err:
            logger.warning("Failed to send CB alert: %s", alert_err)

        self._running = False

    async def _pause_buying_on_error(self, reason: str):
        """Transient 에러 시 매수만 일시정지 (매도/모니터링은 계속)."""
        if self._buy_pause_state == BuyPauseState.PAUSED:
            return  # already paused
        try:
            async with TradingSessionLocal() as session:
                mgr = BuyPauseManager(self.account_id, session)
                await mgr.force_pause(reason=reason)
                await session.commit()
            self._buy_pause_state = BuyPauseState.PAUSED
            logger.warning("Buying paused due to errors: %s", reason)

            alert = get_alert_service()
            await alert.send_critical(
                f"Buying paused (errors)\nAccount: {self.account_id}\nReason: {reason}\nSelling/monitoring continues"
            )
        except Exception as e:
            logger.warning("Failed to pause buying on error: %s", e)

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
