from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID

from app.models.core_btc_history import CoreBtcHistory
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
    "pending_kind",
    "pending_trigger_price",
)


@BuyLogicRegistry.register
class LotStackingBuy(BaseBuyLogic):
    name = "lot_stacking"
    display_name = "LOT \uc801\ub9bd \ub9e4\uc218"
    description = "\uae30\uc900\uac00 \ud558\ub77d \uc2dc \ubd84\ud560 \ub9e4\uc218, \ub9ac\uc800\ube0c BTC \uc801\ub9bd"
    version = "1.0.0"

    default_params = {
        "buy_usdt": 100.0,
        "sizing_mode": "fixed",
        "buy_balance_pct": 10.0,
        "plan_x_pct": 0.5,
        "max_buy_usdt": 500.0,
        "drop_pct": 0.006,
        "prebuy_pct": 0.0015,
        "cancel_rebound_pct": 0.004,
        "min_trade_usdt": 6.0,
        "recenter_enabled": True,
        "recenter_pct": 0.02,
        "recenter_ema_n": 40,
        "use_fixed_usdt_reference": True,
    }

    tunable_params = {
        "sizing_mode": {
            "type": "select",
            "options": [
                {"value": "fixed", "label": "고정 금액"},
                {"value": "pct_balance", "label": "잔고 비율"},
                {"value": "scaled_plan", "label": "단계별 비율"},
            ],
            "title": "매수 금액 모드",
            "group": "sizing",
        },
        "buy_usdt": {
            "type": "float", "min": 10.0, "max": 500.0, "step": 1.0,
            "title": "롯 매수금액", "unit": "USDT",
            "visible_when": {"sizing_mode": "fixed"},
            "group": "sizing",
        },
        "buy_balance_pct": {
            "type": "float", "min": 1.0, "max": 50.0, "step": 0.5,
            "title": "잔고 대비 매수 비율", "unit": "%",
            "visible_when": {"sizing_mode": "pct_balance"},
            "group": "sizing",
        },
        "plan_x_pct": {
            "type": "float", "min": 0.1, "max": 10.0, "step": 0.1,
            "title": "단계별 기본 비율 (x%)", "unit": "%",
            "visible_when": {"sizing_mode": "scaled_plan"},
            "group": "sizing",
        },
        "max_buy_usdt": {
            "type": "float", "min": 10.0, "max": 5000.0, "step": 10.0,
            "title": "최대 매수 금액", "unit": "USDT",
            "visible_when": {"sizing_mode": ["pct_balance", "scaled_plan"]},
            "group": "sizing",
        },
        "drop_pct": {
            "type": "float", "min": 0.006, "max": 0.02, "step": 0.0005,
            "title": "\ud558\ub77d \ud2b8\ub9ac\uac70 \ube44\uc728", "unit": "%",
            "group": "condition",
        },
        "recenter_pct": {
            "type": "float", "min": 0.005, "max": 0.05, "step": 0.0005,
            "title": "\uae30\uc900\uac00 \ub9ac\uc13c\ud130 \ube44\uc728", "unit": "%",
            "group": "condition",
        },
        "recenter_ema_n": {
            "type": "int", "min": 5, "max": 200, "step": 1,
            "title": "EMA \uae30\uac04", "unit": "N",
            "group": "condition",
        },
        "recenter_enabled": {
            "type": "bool",
            "title": "\ub9ac\uc13c\ud130 \ud65c\uc131\ud654",
            "group": "condition",
        },
    }

    # ------------------------------------------------------------------
    # pre_tick / tick
    # ------------------------------------------------------------------

    async def pre_tick(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        repos: RepositoryBundle,
        combo_id: UUID,
    ) -> None:
        await self._maybe_recenter_base(ctx, state, exchange, repos, combo_id)

    async def tick(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        combo_id: UUID,
    ) -> None:
        has_pending = await self._process_pending_buy(
            ctx, state, exchange, account_state, repos, combo_id,
        )
        if not has_pending:
            await self._maybe_buy_on_drop(
                ctx, state, exchange, account_state, repos, combo_id,
            )

    # ------------------------------------------------------------------
    # _process_pending_buy
    # ------------------------------------------------------------------

    async def _process_pending_buy(
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
        pending_kind = await state.get("pending_kind", "LOT")
        pending_trigger = await state.get_float("pending_trigger_price", 0.0)

        try:
            order_data = await exchange.get_order(order_id, ctx.symbol)
        except Exception as exc:
            logger.error("lot_stacking_buy: failed to fetch pending order %s: %s", order_id, exc)
            return True

        await repos.order.upsert_order(ctx.account_id, order_data)
        status = str(order_data.get("status", "")).upper()

        if status == "FILLED":
            logger.info("lot_stacking_buy: pending buy order %s FILLED", order_id)
            await self._handle_filled_buy(
                ctx, state, order_data, account_state, repos, combo_id,
                kind=pending_kind, core_bucket_locked=pending_bucket,
            )
            await state.clear_keys(*_PENDING_KEYS)
            return True

        if status in ("CANCELED", "REJECTED", "EXPIRED"):
            logger.info("lot_stacking_buy: pending buy order %s %s", order_id, status)
            await state.clear_keys(*_PENDING_KEYS)
            return True

        now_ms = int(self._now() * 1000)
        if pending_time_ms > 0 and (now_ms - pending_time_ms) > _PENDING_TIMEOUT_MS:
            logger.warning("lot_stacking_buy: pending buy order %s timed out, cancelling", order_id)
            try:
                cancel_resp = await exchange.cancel_order(order_id, ctx.symbol)
                await repos.order.upsert_order(ctx.account_id, cancel_resp)
            except Exception as exc:
                logger.error("lot_stacking_buy: cancel timed-out order %s failed: %s", order_id, exc)
            await state.clear_keys(*_PENDING_KEYS)
            return True

        if status == "NEW" and pending_kind == "LOT" and pending_trigger > 0:
            cancel_rebound_pct = ctx.params.get("cancel_rebound_pct", 0.004)
            rebound_price = pending_trigger * (1 + cancel_rebound_pct)
            if ctx.current_price >= rebound_price:
                logger.info(
                    "lot_stacking_buy: rebound detected (cur=%.2f >= %.2f), cancelling order %s",
                    ctx.current_price, rebound_price, order_id,
                )
                try:
                    cancel_resp = await exchange.cancel_order(order_id, ctx.symbol)
                    await repos.order.upsert_order(ctx.account_id, cancel_resp)
                except Exception as exc:
                    logger.error("lot_stacking_buy: cancel rebound order %s failed: %s", order_id, exc)
                await state.clear_keys(*_PENDING_KEYS)
                return True

        return True

    # ------------------------------------------------------------------
    # _handle_filled_buy
    # ------------------------------------------------------------------

    async def _handle_filled_buy(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        order_data: dict,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        combo_id: UUID,
        *,
        kind: str = "LOT",
        core_bucket_locked: float = 0.0,
    ) -> None:
        bought_qty = float(order_data.get("executedQty", 0))
        spent_usdt = float(order_data.get("cummulativeQuoteQty", 0))
        order_id = int(order_data.get("orderId", 0))
        update_time_ms = int(order_data.get("updateTime", 0)) or int(self._now() * 1000)

        base_fee_qty = extract_base_commission_qty(order_data, ctx.base_asset)
        bought_qty_net = bought_qty - base_fee_qty
        if bought_qty_net <= 0:
            bought_qty_net = bought_qty

        avg_price = spent_usdt / bought_qty_net if bought_qty_net > 0 else ctx.current_price

        if kind == "INIT":
            await account_state.set_reserve_qty(bought_qty_net)
            await account_state.set_reserve_cost_usdt(spent_usdt)
            await state.set("core_btc_initial", bought_qty_net)

            history = CoreBtcHistory(
                account_id=ctx.account_id,
                symbol=ctx.symbol,
                btc_qty=bought_qty_net,
                cost_usdt=spent_usdt,
                source="INIT",
            )
            state._session.add(history)
            logger.info(
                "lot_stacking_buy: INIT buy filled qty=%.8f cost=%.2f avg=%.2f",
                bought_qty_net, spent_usdt, avg_price,
            )
        else:
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

            # scaled_plan: 회차 증가 + 5회차 금액(A) 저장
            sizing_mode = ctx.params.get("sizing_mode", "fixed")
            if sizing_mode == "scaled_plan":
                cur_round = await state.get_int("sizing_round", 1)
                if cur_round == 5:
                    await state.set("plan_5th_amount", spent_usdt)
                await state.set("sizing_round", cur_round + 1)

            logger.info(
                "lot_stacking_buy: LOT buy filled qty_net=%.8f avg=%.2f",
                bought_qty_net, avg_price,
            )

        await state.set("base_price", avg_price)

    # ------------------------------------------------------------------
    # _maybe_recenter_base
    # ------------------------------------------------------------------

    async def _maybe_recenter_base(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        repos: RepositoryBundle,
        combo_id: UUID,
    ) -> None:
        if not ctx.params.get("recenter_enabled", True):
            return

        open_lots = await repos.lot.get_open_lots_by_combo(ctx.account_id, ctx.symbol, combo_id)
        if open_lots:
            return

        # scaled_plan: open_lots 없고 pending 없으면 회차 리셋
        pending = await state.get("pending_order_id")
        if not pending or str(pending).strip() == "":
            sizing_mode = ctx.params.get("sizing_mode", "fixed")
            if sizing_mode == "scaled_plan":
                await state.set("sizing_round", 1)
                await state.set("plan_5th_amount", "")

        base_price = await state.get_float("base_price", 0.0)
        if base_price <= 0:
            return

        recenter_pct = ctx.params.get("recenter_pct", 0.02)
        recenter_ema_n = ctx.params.get("recenter_ema_n", 40)

        ema = await self._update_recenter_ema(state, ctx.current_price, recenter_ema_n)

        if ema >= base_price * (1 + recenter_pct):
            logger.info(
                "lot_stacking_buy: recentering base_price from %.2f to EMA %.2f (pct=%.4f)",
                base_price, ema, recenter_pct,
            )
            await state.set("base_price", ema)

    async def _update_recenter_ema(
        self, state: StrategyStateStore, price: float, n: int
    ) -> float:
        alpha = 2.0 / (n + 1)
        prev = await state.get_float("recenter_ema", 0.0)
        if prev <= 0:
            ema = price
        else:
            ema = alpha * price + (1 - alpha) * prev
        await state.set("recenter_ema", ema)
        return ema

    # ------------------------------------------------------------------
    # _maybe_buy_on_drop
    # ------------------------------------------------------------------

    async def _maybe_buy_on_drop(
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

        base_price = await state.get_float("base_price", 0.0)
        if base_price <= 0:
            await state.set("base_price", ctx.current_price)
            base_price = ctx.current_price
            logger.info("lot_stacking_buy: initialized base_price to %.2f", base_price)

        drop_pct = ctx.params.get("drop_pct", 0.006)
        prebuy_pct = ctx.params.get("prebuy_pct", 0.0015)
        min_trade_usdt = ctx.params.get("min_trade_usdt", 6.0)

        trigger_price = base_price * (1 - drop_pct)
        prebuy_price = trigger_price * (1 + prebuy_pct)

        if ctx.current_price > prebuy_price:
            return

        filters = await exchange.get_symbol_filters(ctx.symbol)

        free_balance = await exchange.get_free_balance(ctx.quote_asset)
        sizing_round = await state.get_int("sizing_round", 1)
        plan_5th_amt = await state.get_float("plan_5th_amount", 0.0)
        total_buy_usdt = resolve_buy_usdt(
            ctx.params, free_balance, sizing_round, plan_5th_amt,
        )

        if total_buy_usdt < min_trade_usdt:
            logger.warning(
                "lot_stacking_buy: buy_usdt %.2f below min_trade_usdt %.2f",
                total_buy_usdt, min_trade_usdt,
            )
            return

        trigger_adjusted = await exchange.adjust_price(trigger_price, ctx.symbol)
        est_qty = total_buy_usdt / trigger_adjusted if trigger_adjusted > 0 else 0
        if est_qty * trigger_adjusted < filters.min_notional:
            logger.warning("lot_stacking_buy: estimated notional below min_notional")
            return

        try:
            order_resp = await exchange.place_limit_buy_by_quote(
                quote_usdt=total_buy_usdt,
                price=trigger_adjusted,
                symbol=ctx.symbol,
                client_oid=f"{ctx.client_order_prefix}_LOT",
            )
        except Exception as exc:
            logger.error("lot_stacking_buy: place LOT buy failed: %s", exc)
            return

        await repos.order.upsert_order(ctx.account_id, order_resp)
        placed_order_id = int(order_resp.get("orderId", 0))
        placed_time_ms = int(order_resp.get("transactTime", 0)) or int(self._now() * 1000)

        await state.set("pending_order_id", placed_order_id)
        await state.set("pending_time_ms", placed_time_ms)
        await state.set("pending_bucket_usdt", 0)
        await state.set("pending_kind", "LOT")
        await state.set("pending_trigger_price", trigger_adjusted)
        self._touch_order()

        logger.info(
            "lot_stacking_buy: placed LOT buy order %s at trigger=%.2f usdt=%.2f",
            placed_order_id, trigger_adjusted, total_buy_usdt,
        )
