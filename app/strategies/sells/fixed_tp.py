from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.strategies.base import BaseSellLogic, RepositoryBundle, StrategyContext
from app.strategies.registry import SellLogicRegistry
from app.strategies.utils import extract_fee_usdt

if TYPE_CHECKING:
    from app.exchange.base_client import ExchangeClient
    from app.services.account_state_manager import AccountStateManager
    from app.strategies.state_store import StrategyStateStore

logger = logging.getLogger(__name__)

_ORDER_COOLDOWN_SEC = 5.0


@SellLogicRegistry.register
class FixedTpSell(BaseSellLogic):
    name = "fixed_tp"
    display_name = "\uace0\uc815 \ube44\uc728 \uc775\uc808"
    description = "\ub9e4\uc218\uac00 \ub300\ube44 \uace0\uc815 \ube44\uc728 \uc0c1\uc2b9 \uc2dc \uc775\uc808 \ub9e4\ub3c4"
    version = "1.0.0"

    default_params = {
        "tp_pct": 0.033,
        "min_trade_usdt": 6.0,
        "base_price_update_mode": "always",
    }

    tunable_params = {
        "tp_pct": {
            "type": "float", "min": 0.01, "max": 0.1, "step": 0.001,
            "title": "\uc775\uc808 \ube44\uc728", "unit": "%",
            "group": "condition",
        },
        "base_price_update_mode": {
            "type": "select",
            "title": "\uae30\uc900\uac00 \uac31\uc2e0 \ubaa8\ub4dc",
            "options": [
                {"value": "always", "label": "\ud56d\uc0c1 \uac31\uc2e0"},
                {"value": "if_higher", "label": "\uc0c1\uc2b9\uc2dc\ub9cc"},
            ],
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
        open_lots: list,
    ) -> None:
        if not open_lots:
            return

        tp_pct = ctx.params.get("tp_pct", 0.033)
        min_trade_usdt = ctx.params.get("min_trade_usdt", 6.0)
        base_mode = ctx.params.get("base_price_update_mode", "always")
        filters = await exchange.get_symbol_filters(ctx.symbol)

        for lot in open_lots:
            target_price = lot.buy_price * (1 + tp_pct)
            target_price = await exchange.adjust_price(target_price, ctx.symbol)
            sell_qty = await exchange.adjust_qty(lot.buy_qty, ctx.symbol)
            notional = sell_qty * target_price

            if notional < filters.min_notional or notional < min_trade_usdt:
                logger.warning(
                    "fixed_tp: lot %s notional %.2f below minimum, skipping TP",
                    lot.lot_id, notional,
                )
                continue

            if lot.sell_order_id:
                await self._check_existing_sell_order(
                    ctx, state, exchange, account_state, repos,
                    lot, target_price, base_mode,
                )
            else:
                await self._place_new_sell_order(
                    ctx, state, exchange, repos,
                    lot, target_price, sell_qty,
                )

    # ------------------------------------------------------------------
    # _check_existing_sell_order
    # ------------------------------------------------------------------

    async def _check_existing_sell_order(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        account_state: AccountStateManager,
        repos: RepositoryBundle,
        lot,
        target_price: float,
        base_mode: str,
    ) -> None:
        try:
            sell_order_data = await exchange.get_order(lot.sell_order_id, ctx.symbol)
        except Exception as exc:
            logger.error(
                "fixed_tp: failed to get sell order %s for lot %s: %s",
                lot.sell_order_id, lot.lot_id, exc,
            )
            return

        await repos.order.upsert_order(ctx.account_id, sell_order_data)
        sell_status = str(sell_order_data.get("status", "")).upper()

        if sell_status == "FILLED":
            sell_qty_filled = float(sell_order_data.get("executedQty", 0))
            sell_revenue = float(sell_order_data.get("cummulativeQuoteQty", 0))
            sell_price = sell_revenue / sell_qty_filled if sell_qty_filled > 0 else target_price
            sell_time_ms = int(sell_order_data.get("updateTime", 0)) or int(time.time() * 1000)

            fee_usdt = extract_fee_usdt(sell_order_data, ctx.quote_asset)
            cost_usdt = lot.buy_qty * lot.buy_price
            net_profit = sell_revenue - cost_usdt - fee_usdt

            # 양수 수익만 적립금에 추가 (음수 방어)
            if net_profit > 0:
                await account_state.add_pending_earnings(net_profit)

            await repos.lot.close_lot(
                account_id=ctx.account_id,
                lot_id=lot.lot_id,
                sell_price=sell_price,
                sell_time_ms=sell_time_ms,
                fee_usdt=fee_usdt,
                net_profit_usdt=net_profit,
                sell_order_id=lot.sell_order_id,
            )

            # Update base_price per mode
            if base_mode == "always":
                await state.set("base_price", sell_price)
            elif base_mode == "if_higher":
                current_base = await state.get_float("base_price", 0.0)
                if sell_price > current_base:
                    await state.set("base_price", sell_price)

            logger.info(
                "fixed_tp: lot %s TP filled sell=%.2f profit=%.4f pending_earnings+=%.4f",
                lot.lot_id, sell_price, net_profit,
                net_profit if net_profit > 0 else 0.0,
            )

        elif sell_status in ("CANCELED", "REJECTED", "EXPIRED"):
            logger.info(
                "fixed_tp: sell order %s for lot %s %s, clearing",
                lot.sell_order_id, lot.lot_id, sell_status,
            )
            await repos.lot.clear_sell_order(
                account_id=ctx.account_id, lot_id=lot.lot_id,
            )

        elif sell_status == "NEW":
            pass

    # ------------------------------------------------------------------
    # _place_new_sell_order
    # ------------------------------------------------------------------

    async def _place_new_sell_order(
        self,
        ctx: StrategyContext,
        state: StrategyStateStore,
        exchange: ExchangeClient,
        repos: RepositoryBundle,
        lot,
        target_price: float,
        sell_qty: float,
    ) -> None:
        if not self._cooldown_ok(_ORDER_COOLDOWN_SEC):
            return

        try:
            sell_resp = await exchange.place_limit_sell(
                qty_base=sell_qty,
                price=target_price,
                symbol=ctx.symbol,
                client_oid=f"{ctx.client_order_prefix}_TP_{lot.lot_id}",
            )
        except Exception as exc:
            logger.error(
                "fixed_tp: place TP sell for lot %s failed: %s",
                lot.lot_id, exc,
            )
            return

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
            "fixed_tp: placed TP sell order %s for lot %s at %.2f qty=%.8f",
            sell_order_id, lot.lot_id, target_price, sell_qty,
        )
