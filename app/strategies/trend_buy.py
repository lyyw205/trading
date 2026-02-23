from __future__ import annotations

import time
import logging
from typing import TYPE_CHECKING

from app.strategies.base import BaseStrategy, StrategyContext, RepositoryBundle
from app.strategies.registry import StrategyRegistry
from app.strategies.state_store import StrategyStateStore
from app.models.core_btc_history import CoreBtcHistory

if TYPE_CHECKING:
    from app.exchange.base_client import ExchangeClient
    from app.services.account_state_manager import AccountStateManager

logger = logging.getLogger(__name__)

# Pending buy timeout: 3 hours in milliseconds
_PENDING_TIMEOUT_MS = 3 * 60 * 60 * 1000
# Minimum cooldown between orders in seconds
_ORDER_COOLDOWN_SEC = 5.0

_PENDING_KEYS = (
    "pending_order_id",
    "pending_time_ms",
    "pending_trend_bucket_usdt",
    "pending_trigger_price",
)


@StrategyRegistry.register
class TrendBuyStrategy(BaseStrategy):
    name = "trend_buy"
    display_name = "추세 매수"
    description = "상승 추세 되돌림 시 분할 매수, 순이익으로 리저브 BTC 적립"
    version = "1.0.0"

    default_params = {
        "buy_usdt": 50.0,
        "tp_pct": 0.03,
        "enable_pct": 0.03,
        "recenter_pct": 0.02,
        "drop_pct": 0.01,
        "step_pct": 0.01,
        "min_trade_usdt": 6.0,
    }

    tunable_params = {
        "buy_usdt": {
            "type": "float", "min": 10.0, "max": 500.0, "step": 1.0,
            "title": "추세 매수금액", "unit": "USDT",
        },
        "enable_pct": {
            "type": "float", "min": 0.01, "max": 0.1, "step": 0.005,
            "title": "추세 활성화 기준", "unit": "%",
        },
        "drop_pct": {
            "type": "float", "min": 0.005, "max": 0.05, "step": 0.001,
            "title": "되돌림 매수 비율", "unit": "%",
        },
    }

    # ------------------------------------------------------------------
    # tick: main entry point called every cycle
    # ------------------------------------------------------------------

    async def tick(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
    ) -> None:
        has_pending = await self._process_pending_trend_buy(ctx, state, exchange, account_state, repos)
        await self._maybe_take_profit_trends(ctx, state, exchange, account_state, repos)
        if not has_pending:
            await self._maybe_buy_on_trend(ctx, state, exchange, account_state, repos)

    async def on_fill(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        fill_data: dict,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
    ) -> None:
        """External fill notification handler (currently delegates to tick flow)."""
        pass

    # ------------------------------------------------------------------
    # _process_pending_trend_buy  (port of btc_trader L719-761)
    # ------------------------------------------------------------------

    async def _process_pending_trend_buy(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
    ) -> bool:
        pending_order_id = await state.get("pending_order_id")
        if not pending_order_id or str(pending_order_id).strip() == "":
            return False

        order_id = int(pending_order_id)
        pending_time_ms = await state.get_int("pending_time_ms", 0)
        pending_trend_bucket = await state.get_float("pending_trend_bucket_usdt", 0.0)
        pending_trigger = await state.get_float("pending_trigger_price", 0.0)

        try:
            order_data = await exchange.get_order(order_id, ctx.symbol)
        except Exception as exc:
            logger.error("trend_buy: failed to fetch pending order %s: %s", order_id, exc)
            return True  # still has pending, retry next tick

        await repos.order.upsert_order(ctx.account_id, order_data)
        status = str(order_data.get("status", "")).upper()

        # -- FILLED --
        if status == "FILLED":
            logger.info("trend_buy: pending trend buy order %s FILLED", order_id)
            await self._handle_filled_trend_buy(
                ctx, state, order_data, account_state, repos,
                core_bucket_locked=pending_trend_bucket,
            )
            await state.clear_keys(*_PENDING_KEYS)
            return True

        # -- CANCELED / REJECTED / EXPIRED --
        if status in ("CANCELED", "REJECTED", "EXPIRED"):
            logger.info("trend_buy: pending trend buy order %s %s", order_id, status)
            await state.clear_keys(*_PENDING_KEYS)
            return True

        # -- Timeout check (3 hours) --
        now_ms = int(time.time() * 1000)
        if pending_time_ms > 0 and (now_ms - pending_time_ms) > _PENDING_TIMEOUT_MS:
            logger.warning("trend_buy: pending trend buy order %s timed out, cancelling", order_id)
            try:
                cancel_resp = await exchange.cancel_order(order_id, ctx.symbol)
                await repos.order.upsert_order(ctx.account_id, cancel_resp)
            except Exception as exc:
                logger.error("trend_buy: cancel timed-out order %s failed: %s", order_id, exc)
            await state.clear_keys(*_PENDING_KEYS)
            return True

        # No rebound cancel for trend buy (unlike LOT)
        return True  # still has pending order

    # ------------------------------------------------------------------
    # _handle_filled_trend_buy  (port of btc_trader L590-659)
    # ------------------------------------------------------------------

    async def _handle_filled_trend_buy(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        order_data: dict,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        *,
        core_bucket_locked: float = 0.0,
    ) -> None:
        bought_qty = float(order_data.get("executedQty", 0))
        spent_usdt = float(order_data.get("cummulativeQuoteQty", 0))
        order_id = int(order_data.get("orderId", 0))
        update_time_ms = int(order_data.get("updateTime", 0)) or int(time.time() * 1000)

        # Extract base-asset commission from fills
        base_fee_qty = self._extract_base_commission_qty(order_data, ctx.base_asset)
        bought_qty_net = bought_qty - base_fee_qty
        if bought_qty_net <= 0:
            bought_qty_net = bought_qty

        avg_price = spent_usdt / bought_qty_net if bought_qty_net > 0 else ctx.current_price

        # Core bucket allocation
        core_used = min(core_bucket_locked, spent_usdt)
        core_btc_add = 0.0
        if core_used > 0 and spent_usdt > 0:
            core_btc_add = bought_qty_net * (core_used / spent_usdt)
            await account_state.add_reserve_qty(core_btc_add)
            await account_state.add_reserve_cost_usdt(core_used)

            # Record core_btc_history for the core portion
            history = CoreBtcHistory(
                account_id=ctx.account_id,
                symbol=ctx.symbol,
                btc_qty=core_btc_add,
                cost_usdt=core_used,
                source="TREND",
            )
            state._session.add(history)

        # Deduct used amount from core_bucket
        prev_bucket = await state.get_float("core_bucket_usdt", 0.0)
        new_bucket = prev_bucket - core_used
        await state.set("core_bucket_usdt", new_bucket)

        # Lot BTC (excluding core portion)
        lot_btc_qty = bought_qty_net - core_btc_add

        if lot_btc_qty > 0:
            await repos.lot.insert_lot(
                account_id=ctx.account_id,
                symbol=ctx.symbol,
                strategy_name="trend_buy",
                buy_order_id=order_id,
                buy_price=avg_price,
                buy_qty=lot_btc_qty,
                buy_time_ms=update_time_ms,
            )

        # Update trend_base_price if avg_price > current trend_base
        current_trend_base = await state.get_float("base_price", 0.0)
        if avg_price > current_trend_base:
            await state.set("base_price", avg_price)

        # Set last_buy_price
        await state.set("last_buy_price", avg_price)

        logger.info(
            "trend_buy: TREND buy filled qty_net=%.8f core_add=%.8f lot_qty=%.8f avg=%.2f",
            bought_qty_net, core_btc_add, lot_btc_qty, avg_price,
        )

    # ------------------------------------------------------------------
    # _maybe_take_profit_trends  (port of btc_trader L1183-1300)
    # ------------------------------------------------------------------

    async def _maybe_take_profit_trends(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
    ) -> None:
        open_lots = await repos.lot.get_open_lots(ctx.account_id, ctx.symbol, "trend_buy")
        if not open_lots:
            return

        tp_pct = ctx.params.get("tp_pct", 0.03)
        min_trade_usdt = ctx.params.get("min_trade_usdt", 6.0)
        filters = await exchange.get_symbol_filters(ctx.symbol)

        for lot in open_lots:
            target_price = lot.buy_price * (1 + tp_pct)
            target_price = await exchange.adjust_price(target_price, ctx.symbol)
            sell_qty = await exchange.adjust_qty(lot.buy_qty, ctx.symbol)
            notional = sell_qty * target_price

            if notional < filters.min_notional or notional < min_trade_usdt:
                logger.warning(
                    "trend_buy: lot %s notional %.2f below minimum, skipping TP",
                    lot.lot_id, notional,
                )
                continue

            # -- Lot already has a sell order --
            if lot.sell_order_id:
                try:
                    sell_order_data = await exchange.get_order(lot.sell_order_id, ctx.symbol)
                except Exception as exc:
                    logger.error(
                        "trend_buy: failed to get sell order %s for lot %s: %s",
                        lot.sell_order_id, lot.lot_id, exc,
                    )
                    continue

                await repos.order.upsert_order(ctx.account_id, sell_order_data)
                sell_status = str(sell_order_data.get("status", "")).upper()

                if sell_status == "FILLED":
                    sell_qty_filled = float(sell_order_data.get("executedQty", 0))
                    sell_revenue = float(sell_order_data.get("cummulativeQuoteQty", 0))
                    sell_price = sell_revenue / sell_qty_filled if sell_qty_filled > 0 else target_price
                    sell_time_ms = int(sell_order_data.get("updateTime", 0)) or int(time.time() * 1000)

                    fee_usdt = self._extract_fee_usdt(sell_order_data, ctx.quote_asset)
                    cost_usdt = lot.buy_qty * lot.buy_price
                    net_profit = sell_revenue - cost_usdt - fee_usdt

                    # Add net profit to trend_buy core_bucket
                    prev_bucket = await state.get_float("core_bucket_usdt", 0.0)
                    await state.set("core_bucket_usdt", prev_bucket + net_profit)

                    await repos.lot.close_lot(
                        account_id=ctx.account_id,
                        lot_id=lot.lot_id,
                        sell_price=sell_price,
                        sell_time_ms=sell_time_ms,
                        fee_usdt=fee_usdt,
                        net_profit_usdt=net_profit,
                        sell_order_id=lot.sell_order_id,
                    )

                    # Update trend_base_price if sell_price > current base
                    current_trend_base = await state.get_float("base_price", 0.0)
                    if sell_price > current_trend_base:
                        await state.set("base_price", sell_price)

                    logger.info(
                        "trend_buy: lot %s TP filled sell=%.2f profit=%.4f bucket=%.4f",
                        lot.lot_id, sell_price, net_profit, prev_bucket + net_profit,
                    )

                elif sell_status in ("CANCELED", "REJECTED", "EXPIRED"):
                    logger.info(
                        "trend_buy: sell order %s for lot %s %s, clearing",
                        lot.sell_order_id, lot.lot_id, sell_status,
                    )
                    await repos.lot.clear_sell_order(
                        account_id=ctx.account_id, lot_id=lot.lot_id,
                    )

                elif sell_status == "NEW":
                    # Order still open, skip
                    pass

            # -- No sell order yet: place one --
            else:
                if not self._cooldown_ok(_ORDER_COOLDOWN_SEC):
                    continue

                try:
                    sell_resp = await exchange.place_limit_sell(
                        qty_base=sell_qty,
                        price=target_price,
                        symbol=ctx.symbol,
                        client_oid=f"{ctx.client_order_prefix}_TTP_{lot.lot_id}",
                    )
                except Exception as exc:
                    logger.error(
                        "trend_buy: place TP sell for lot %s failed: %s",
                        lot.lot_id, exc,
                    )
                    continue

                await repos.order.upsert_order(ctx.account_id, sell_resp)
                sell_order_id = int(sell_resp.get("orderId", 0))
                sell_time_ms = int(sell_resp.get("transactTime", 0)) or int(time.time() * 1000)

                await repos.lot.set_sell_order(
                    account_id=ctx.account_id,
                    lot_id=lot.lot_id,
                    sell_order_id=sell_order_id,
                    sell_order_time_ms=sell_time_ms,
                )
                self._touch_order()

                logger.info(
                    "trend_buy: placed TP sell order %s for lot %s at %.2f qty=%.8f",
                    sell_order_id, lot.lot_id, target_price, sell_qty,
                )

    # ------------------------------------------------------------------
    # _maybe_buy_on_trend  (port of btc_trader L979-1054)
    # ------------------------------------------------------------------

    async def _maybe_buy_on_trend(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
    ) -> None:
        if not self._cooldown_ok(_ORDER_COOLDOWN_SEC):
            return

        # CRITICAL: Read LOT base_price from lot_stacking scope to determine trend activation
        lot_state = StrategyStateStore(ctx.account_id, "lot_stacking", state._session)
        lot_base_price = await lot_state.get_float("base_price", 0.0)
        if lot_base_price <= 0:
            return

        enable_pct = ctx.params.get("enable_pct", 0.03)
        recenter_pct = ctx.params.get("recenter_pct", 0.02)
        drop_pct = ctx.params.get("drop_pct", 0.01)
        step_pct = ctx.params.get("step_pct", 0.01)
        min_trade_usdt = ctx.params.get("min_trade_usdt", 6.0)
        buy_usdt = ctx.params.get("buy_usdt", 50.0)

        # Check trend activation: current price must be above LOT base by enable_pct
        if ctx.current_price < lot_base_price * (1 + enable_pct):
            return

        # Get/set trend_base_price
        trend_base = await state.get_float("base_price", 0.0)
        if trend_base <= 0:
            trend_base = ctx.current_price
            await state.set("base_price", trend_base)
            logger.info("trend_buy: initialized trend_base_price to %.2f", trend_base)

        # Recenter: if price has risen above trend_base * (1 + recenter_pct), update trend_base
        if ctx.current_price >= trend_base * (1 + recenter_pct):
            logger.info(
                "trend_buy: recentering trend_base from %.2f to %.2f",
                trend_base, ctx.current_price,
            )
            trend_base = ctx.current_price
            await state.set("base_price", trend_base)

        # Calculate target buy price on pullback from trend_base
        target_buy_price = trend_base * (1 - drop_pct)

        if ctx.current_price > target_buy_price:
            return

        # Check step_pct: avoid buying too close to last buy
        last_buy_price = await state.get_float("last_buy_price", 0.0)
        if last_buy_price > 0 and ctx.current_price > last_buy_price * (1 - step_pct):
            return

        # Get symbol filters
        filters = await exchange.get_symbol_filters(ctx.symbol)

        # Add core_bucket to buy amount
        core_bucket = await state.get_float("core_bucket_usdt", 0.0)
        total_buy_usdt = buy_usdt + max(0.0, core_bucket)

        if total_buy_usdt < min_trade_usdt:
            logger.warning(
                "trend_buy: buy_usdt %.2f below min_trade_usdt %.2f",
                total_buy_usdt, min_trade_usdt,
            )
            return

        # Adjust trigger price
        trigger_adjusted = await exchange.adjust_price(target_buy_price, ctx.symbol)

        # Check min_notional
        est_qty = total_buy_usdt / trigger_adjusted if trigger_adjusted > 0 else 0
        if est_qty * trigger_adjusted < filters.min_notional:
            logger.warning("trend_buy: estimated notional below min_notional")
            return

        try:
            order_resp = await exchange.place_limit_buy_by_quote(
                quote_usdt=total_buy_usdt,
                price=trigger_adjusted,
                symbol=ctx.symbol,
                client_oid=f"{ctx.client_order_prefix}_TREND",
            )
        except Exception as exc:
            logger.error("trend_buy: place TREND buy failed: %s", exc)
            return

        await repos.order.upsert_order(ctx.account_id, order_resp)
        placed_order_id = int(order_resp.get("orderId", 0))
        placed_time_ms = int(order_resp.get("transactTime", 0)) or int(time.time() * 1000)

        # Set pending buy state
        await state.set("pending_order_id", placed_order_id)
        await state.set("pending_time_ms", placed_time_ms)
        await state.set("pending_trend_bucket_usdt", max(0.0, core_bucket))
        await state.set("pending_trigger_price", trigger_adjusted)
        self._touch_order()

        logger.info(
            "trend_buy: placed TREND buy order %s at trigger=%.2f usdt=%.2f (core=%.2f)",
            placed_order_id, trigger_adjusted, total_buy_usdt, max(0.0, core_bucket),
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_base_commission_qty(order_data: dict, base_asset: str) -> float:
        """Extract total commission paid in base asset from order fills."""
        fills = order_data.get("fills", [])
        total = 0.0
        for fill in fills:
            if str(fill.get("commissionAsset", "")).upper() == base_asset.upper():
                total += float(fill.get("commission", 0))
        return total

    @staticmethod
    def _extract_fee_usdt(order_data: dict, quote_asset: str) -> float:
        """Extract total fee in quote asset (USDT) from order fills."""
        fills = order_data.get("fills", [])
        total = 0.0
        for fill in fills:
            if str(fill.get("commissionAsset", "")).upper() == quote_asset.upper():
                total += float(fill.get("commission", 0))
        return total

    def _cooldown_ok(self, cooldown_sec: float) -> bool:
        """Return True if enough time has passed since the last order."""
        return (time.time() - self._last_order_ts) >= cooldown_sec

    def _touch_order(self) -> None:
        """Record current time as the last order timestamp."""
        self._last_order_ts = time.time()
