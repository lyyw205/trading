from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID

from app.strategies.base import BaseBuyLogic, RepositoryBundle, StrategyContext
from app.strategies.registry import BuyLogicRegistry
from app.strategies.sizing import resolve_buy_usdt
from app.strategies.utils import extract_base_commission_qty

if TYPE_CHECKING:
    from app.exchange.base_client import ExchangeClient
    from app.services.account_state_manager import AccountStateManager
    from app.strategies.state_store import StrategyStateStore

logger = logging.getLogger(__name__)

_PENDING_TIMEOUT_MS = 3 * 60 * 60 * 1000
_ORDER_COOLDOWN_SEC = 5.0

_PENDING_KEYS = (
    "pending_order_id",
    "pending_time_ms",
    "pending_bucket_usdt",
    "pending_trigger_price",
)


@BuyLogicRegistry.register
class TrendBuy(BaseBuyLogic):
    name = "trend_buy"
    display_name = "\ucd94\uc138 \ub9e4\uc218"
    description = "\uc0c1\uc2b9 \ucd94\uc138 \ub418\ub3cc\ub9bc \uc2dc \ubd84\ud560 \ub9e4\uc218"
    version = "1.0.0"

    default_params = {
        "buy_usdt": 50.0,
        "sizing_mode": "fixed",
        "buy_balance_pct": 10.0,
        "max_buy_usdt": 500.0,
        "enable_pct": 0.03,
        "recenter_pct": 0.02,
        "drop_pct": 0.01,
        "step_pct": 0.01,
        "min_trade_usdt": 6.0,
    }

    tunable_params = {
        "sizing_mode": {
            "type": "select",
            "options": [
                {"value": "fixed", "label": "고정 금액"},
                {"value": "pct_balance", "label": "잔고 비율"},
            ],
            "title": "매수 금액 모드",
            "group": "sizing",
        },
        "buy_usdt": {
            "type": "float", "min": 10.0, "max": 500.0, "step": 1.0,
            "title": "추세 매수금액", "unit": "USDT",
            "visible_when": {"sizing_mode": "fixed"},
            "group": "sizing",
        },
        "buy_balance_pct": {
            "type": "float", "min": 1.0, "max": 50.0, "step": 0.5,
            "title": "잔고 대비 매수 비율", "unit": "%",
            "visible_when": {"sizing_mode": "pct_balance"},
            "group": "sizing",
        },
        "max_buy_usdt": {
            "type": "float", "min": 10.0, "max": 5000.0, "step": 10.0,
            "title": "최대 매수 금액", "unit": "USDT",
            "visible_when": {"sizing_mode": "pct_balance"},
            "group": "sizing",
        },
        "enable_pct": {
            "type": "float", "min": 0.01, "max": 0.1, "step": 0.005,
            "title": "\ucd94\uc138 \ud65c\uc131\ud654 \uae30\uc900", "unit": "%",
            "group": "condition",
        },
        "recenter_pct": {
            "type": "float", "min": 0.005, "max": 0.05, "step": 0.005,
            "title": "\ucd94\uc138 \ub9ac\uc13c\ud130 \ube44\uc728", "unit": "%",
            "group": "condition",
        },
        "drop_pct": {
            "type": "float", "min": 0.005, "max": 0.05, "step": 0.001,
            "title": "\ub418\ub3cc\ub9bc \ub9e4\uc218 \ube44\uc728", "unit": "%",
            "group": "condition",
        },
        "step_pct": {
            "type": "float", "min": 0.005, "max": 0.05, "step": 0.001,
            "title": "\ub2e8\uacc4\ubcc4 \uac04\uaca9", "unit": "%",
            "group": "condition",
        },
    }

    # ------------------------------------------------------------------
    # tick
    # ------------------------------------------------------------------

    async def tick(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        combo_id: UUID,
    ) -> None:
        has_pending = await self._process_pending_trend_buy(
            ctx, state, exchange, account_state, repos, combo_id,
        )
        if not has_pending:
            await self._maybe_buy_on_trend(
                ctx, state, exchange, account_state, repos, combo_id,
            )

    # ------------------------------------------------------------------
    # _process_pending_trend_buy
    # ------------------------------------------------------------------

    async def _process_pending_trend_buy(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        combo_id: UUID,
    ) -> bool:
        pending_order_id = await state.get("pending_order_id")
        if not pending_order_id or str(pending_order_id).strip() == "":
            return False

        order_id = int(pending_order_id)
        pending_time_ms = await state.get_int("pending_time_ms", 0)
        pending_bucket = await state.get_float("pending_bucket_usdt", 0.0)
        pending_trigger = await state.get_float("pending_trigger_price", 0.0)

        try:
            order_data = await exchange.get_order(order_id, ctx.symbol)
        except Exception as exc:
            logger.error("trend_buy: failed to fetch pending order %s: %s", order_id, exc)
            return True

        await repos.order.upsert_order(ctx.account_id, order_data)
        status = str(order_data.get("status", "")).upper()

        if status == "FILLED":
            logger.info("trend_buy: pending trend buy order %s FILLED", order_id)
            await self._handle_filled_trend_buy(
                ctx, state, order_data, account_state, repos, combo_id,
                core_bucket_locked=pending_bucket,
            )
            await state.clear_keys(*_PENDING_KEYS)
            return True

        if status in ("CANCELED", "REJECTED", "EXPIRED"):
            logger.info("trend_buy: pending trend buy order %s %s", order_id, status)
            await state.clear_keys(*_PENDING_KEYS)
            return True

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

        return True

    # ------------------------------------------------------------------
    # _handle_filled_trend_buy
    # ------------------------------------------------------------------

    async def _handle_filled_trend_buy(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        order_data: dict,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        combo_id: UUID,
        *,
        core_bucket_locked: float = 0.0,
    ) -> None:
        bought_qty = float(order_data.get("executedQty", 0))
        spent_usdt = float(order_data.get("cummulativeQuoteQty", 0))
        order_id = int(order_data.get("orderId", 0))
        update_time_ms = int(order_data.get("updateTime", 0)) or int(time.time() * 1000)

        base_fee_qty = extract_base_commission_qty(order_data, ctx.base_asset)
        bought_qty_net = bought_qty - base_fee_qty
        if bought_qty_net <= 0:
            bought_qty_net = bought_qty

        avg_price = spent_usdt / bought_qty_net if bought_qty_net > 0 else ctx.current_price

        # reserve 자동 변환 제거 - 전체 매수 수량을 lot으로 생성
        await repos.lot.insert_lot(
            account_id=ctx.account_id,
            symbol=ctx.symbol,
            strategy_name=self.name,
            buy_order_id=order_id,
            buy_price=avg_price,
            buy_qty=bought_qty_net,
            buy_time_ms=update_time_ms,
            combo_id=combo_id,
        )

        current_trend_base = await state.get_float("base_price", 0.0)
        if avg_price > current_trend_base:
            await state.set("base_price", avg_price)

        await state.set("last_buy_price", avg_price)

        logger.info(
            "trend_buy: TREND buy filled qty_net=%.8f avg=%.2f",
            bought_qty_net, avg_price,
        )

    # ------------------------------------------------------------------
    # _maybe_buy_on_trend
    # ------------------------------------------------------------------

    async def _maybe_buy_on_trend(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        combo_id: UUID,
    ) -> None:
        if not self._cooldown_ok(_ORDER_COOLDOWN_SEC):
            return

        # Cross-combo reference: read lot_stacking base_price via reference_combo_id
        from app.strategies.state_store import StrategyStateStore as SSStore
        ref_combo_id = ctx.params.get("_reference_combo_id")
        if not ref_combo_id:
            logger.warning("trend_buy: no reference_combo_id configured, skipping")
            return

        ref_state = SSStore(ctx.account_id, str(ref_combo_id), state._session)
        lot_base_price = await ref_state.get_float("base_price", 0.0)
        if lot_base_price <= 0:
            return

        enable_pct = ctx.params.get("enable_pct", 0.03)
        recenter_pct = ctx.params.get("recenter_pct", 0.02)
        drop_pct = ctx.params.get("drop_pct", 0.01)
        step_pct = ctx.params.get("step_pct", 0.01)
        min_trade_usdt = ctx.params.get("min_trade_usdt", 6.0)

        if ctx.current_price < lot_base_price * (1 + enable_pct):
            return

        trend_base = await state.get_float("base_price", 0.0)
        if trend_base <= 0:
            trend_base = ctx.current_price
            await state.set("base_price", trend_base)
            logger.info("trend_buy: initialized trend_base_price to %.2f", trend_base)

        if ctx.current_price >= trend_base * (1 + recenter_pct):
            logger.info(
                "trend_buy: recentering trend_base from %.2f to %.2f",
                trend_base, ctx.current_price,
            )
            trend_base = ctx.current_price
            await state.set("base_price", trend_base)

        target_buy_price = trend_base * (1 - drop_pct)

        if ctx.current_price > target_buy_price:
            return

        last_buy_price = await state.get_float("last_buy_price", 0.0)
        if last_buy_price > 0 and ctx.current_price > last_buy_price * (1 - step_pct):
            return

        filters = await exchange.get_symbol_filters(ctx.symbol)

        free_balance = await exchange.get_free_balance(ctx.quote_asset)
        total_buy_usdt = resolve_buy_usdt(ctx.params, free_balance)

        if total_buy_usdt < min_trade_usdt:
            logger.warning(
                "trend_buy: buy_usdt %.2f below min_trade_usdt %.2f",
                total_buy_usdt, min_trade_usdt,
            )
            return

        trigger_adjusted = await exchange.adjust_price(target_buy_price, ctx.symbol)

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

        await state.set("pending_order_id", placed_order_id)
        await state.set("pending_time_ms", placed_time_ms)
        await state.set("pending_bucket_usdt", 0)
        await state.set("pending_trigger_price", trigger_adjusted)
        self._touch_order()

        logger.info(
            "trend_buy: placed TREND buy order %s at trigger=%.2f usdt=%.2f",
            placed_order_id, trigger_adjusted, total_buy_usdt,
        )
