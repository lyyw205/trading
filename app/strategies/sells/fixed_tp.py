from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from app.services.buy_pause_manager import MIN_TRADE_USDT
from app.strategies.base import BaseSellLogic, RepositoryBundle, StrategyContext
from app.strategies.registry import SellLogicRegistry
from app.strategies.utils import extract_fee_usdt

if TYPE_CHECKING:
    from app.exchange.base_client import ExchangeClient
    from app.models.lot import Lot
    from app.services.account_state_manager import AccountStateManager
    from app.strategies.state_store import StrategyStateStore

logger = logging.getLogger(__name__)

_ORDER_COOLDOWN_SEC = 5.0
_MAX_SELL_RETRIES = 3
_SELL_RETRY_COOLDOWN_SEC = 300.0  # 5 minutes


@SellLogicRegistry.register
class FixedTpSell(BaseSellLogic):
    name = "fixed_tp"
    display_name = "\uace0\uc815 \ube44\uc728 \uc775\uc808"
    description = (
        "\ub9e4\uc218\uac00 \ub300\ube44 \uace0\uc815 \ube44\uc728 \uc0c1\uc2b9 \uc2dc \uc775\uc808 \ub9e4\ub3c4"
    )
    version = "1.0.0"

    default_params = {
        "tp_pct": 0.033,
        "min_trade_usdt": MIN_TRADE_USDT,
        "base_price_update_mode": "always",
    }

    tunable_params = {
        "tp_pct": {
            "type": "float",
            "min": 0.01,
            "max": 0.1,
            "step": 0.001,
            "title": "\uc775\uc808 \ube44\uc728",
            "unit": "%",
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
        open_lots: list[Lot],
    ) -> None:
        if not open_lots:
            return

        tp_pct = float(ctx.params.get("tp_pct", 0.033))
        min_trade_usdt = float(ctx.params.get("min_trade_usdt", MIN_TRADE_USDT))
        base_mode = ctx.params.get("base_price_update_mode", "always")
        filters = await exchange.get_symbol_filters(ctx.symbol)

        for lot in open_lots:
            target_price = float(lot.buy_price) * (1 + tp_pct)
            target_price = await exchange.adjust_price(target_price, ctx.symbol)
            sell_qty = await exchange.adjust_qty(float(lot.buy_qty), ctx.symbol)
            notional = sell_qty * target_price

            if notional < filters.min_notional or notional < min_trade_usdt:
                logger.warning(
                    "fixed_tp: lot %s notional %.2f below minimum, skipping TP",
                    lot.lot_id,
                    notional,
                )
                continue

            if lot.sell_order_id:
                await self._check_existing_sell_order(
                    ctx,
                    state,
                    exchange,
                    account_state,
                    repos,
                    lot,
                    target_price,
                    base_mode,
                )
            else:
                await self._place_new_sell_order(
                    ctx,
                    state,
                    exchange,
                    repos,
                    lot,
                    target_price,
                    sell_qty,
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
        lot: Lot,
        target_price: float,
        base_mode: str,
    ) -> None:
        # Always query exchange for authoritative status (DB may be stale for
        # BacktestClient where _check_order_fills updates order status in-place
        # after upsert_order has already saved the initial NEW status).
        db_order = None
        try:
            sell_order_data = await exchange.get_order(lot.sell_order_id, ctx.symbol)
            await repos.order.upsert_order(ctx.account_id, sell_order_data)
        except Exception as exc:
            # Fallback to DB if exchange query fails (production resilience)
            db_order = await repos.order.get_order(ctx.account_id, lot.sell_order_id)
            if db_order:
                sell_order_data = {
                    "orderId": db_order.order_id,
                    "status": db_order.status,
                    "executedQty": str(db_order.executed_qty or 0),
                    "cummulativeQuoteQty": str(db_order.cumulative_quote_qty or 0),
                    "updateTime": db_order.update_time_ms or 0,
                }
            else:
                logger.error(
                    "fixed_tp: failed to get sell order %s for lot %s: %s",
                    lot.sell_order_id,
                    lot.lot_id,
                    exc,
                )
                return
        sell_status = str(sell_order_data.get("status", "")).upper()

        if sell_status == "FILLED":
            sell_qty_filled = float(sell_order_data.get("executedQty", 0))
            sell_revenue = float(sell_order_data.get("cummulativeQuoteQty", 0))
            sell_price = sell_revenue / sell_qty_filled if sell_qty_filled > 0 else target_price
            sell_time_ms = int(sell_order_data.get("updateTime", 0)) or int(self._now() * 1000)

            fee_usdt = extract_fee_usdt(sell_order_data, ctx.quote_asset)
            # DB 경로에서는 fills 키가 없어 extract_fee_usdt가 0을 반환하므로,
            # Fill 테이블에서 직접 수수료를 조회한다.
            if fee_usdt == 0 and db_order:
                fill_rows = await repos.order.get_fills_for_order(
                    ctx.account_id,
                    lot.sell_order_id,
                )
                fee_usdt = sum(
                    float(r.commission or 0)
                    for r in fill_rows
                    if str(r.commission_asset or "").upper() == ctx.quote_asset.upper()
                )
            cost_usdt = float(lot.buy_qty) * float(lot.buy_price)
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

            # Reset recenter EMA to sell_price to prevent stale high EMA
            # from pushing base_price upward on the next pre_tick
            await state.set("recenter_ema", sell_price)

            # Clean up retry state for this lot
            await state.clear_keys(
                f"sell_retry_count:{lot.lot_id}",
                f"sell_retry_after:{lot.lot_id}",
            )

            logger.info(
                "fixed_tp: lot %s TP filled sell=%.2f profit=%.4f pending_earnings+=%.4f",
                lot.lot_id,
                sell_price,
                net_profit,
                net_profit if net_profit > 0 else 0.0,
            )

        elif sell_status in ("CANCELED", "REJECTED", "EXPIRED"):
            logger.info(
                "fixed_tp: sell order %s for lot %s %s, clearing",
                lot.sell_order_id,
                lot.lot_id,
                sell_status,
            )
            await repos.lot.clear_sell_order(
                account_id=ctx.account_id,
                lot_id=lot.lot_id,
            )
            # Clean up retry state for this lot
            await state.clear_keys(
                f"sell_retry_count:{lot.lot_id}",
                f"sell_retry_after:{lot.lot_id}",
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
        lot: Lot,
        target_price: float,
        sell_qty: float,
    ) -> None:
        if self._sim_time is None and not self._cooldown_ok(_ORDER_COOLDOWN_SEC):
            return

        # --- Retry limiting (per-lot) ---
        retry_count = await state.get_int(f"sell_retry_count:{lot.lot_id}", 0)
        if retry_count >= _MAX_SELL_RETRIES:
            retry_after = await state.get_float(f"sell_retry_after:{lot.lot_id}", 0.0)
            if self._now() < retry_after:
                logger.debug(
                    "fixed_tp: lot %s sell retry cooldown active (%d failures), skipping until %.0f",
                    lot.lot_id,
                    retry_count,
                    retry_after,
                )
                return
            # Cooldown expired -- reset counter for fresh retries
            retry_count = 0
            await state.set(f"sell_retry_count:{lot.lot_id}", 0)

        try:
            sell_resp = await exchange.place_limit_sell(
                qty_base=sell_qty,
                price=target_price,
                symbol=ctx.symbol,
                client_oid=f"{ctx.client_order_prefix}_TP_{lot.lot_id}",
            )
        except Exception as exc:
            new_count = retry_count + 1
            await state.set_many(
                {
                    f"sell_retry_count:{lot.lot_id}": new_count,
                    f"sell_retry_after:{lot.lot_id}": self._now() + _SELL_RETRY_COOLDOWN_SEC,
                }
            )
            logger.error(
                "fixed_tp: place TP sell for lot %s failed (%d/%d): %s",
                lot.lot_id,
                new_count,
                _MAX_SELL_RETRIES,
                exc,
            )
            return

        sell_order_id = int(sell_resp.get("orderId", 0))
        sell_time_ms = int(sell_resp.get("transactTime", 0)) or int(self._now() * 1000)

        try:
            await repos.order.upsert_order(ctx.account_id, sell_resp)
            await repos.lot.set_sell_order(
                account_id=ctx.account_id,
                lot_id=lot.lot_id,
                sell_order_id=sell_order_id,
                sell_order_time_ms=sell_time_ms,
            )
            await repos.lot.flush()
        except Exception as db_exc:
            # Do NOT cancel the Binance order -- orphan recovery will
            # reconcile it on the next cycle via clientOrderId matching.
            # Increment retry counter to prevent duplicate place_limit_sell calls
            # while the orphan is unreconciled.
            new_count = retry_count + 1
            with contextlib.suppress(Exception):
                await state.set_many(
                    {
                        f"sell_retry_count:{lot.lot_id}": new_count,
                        f"sell_retry_after:{lot.lot_id}": self._now() + _SELL_RETRY_COOLDOWN_SEC,
                    }
                )
            logger.critical(
                "fixed_tp: FLUSH FAILED after placing sell order %s for lot %s. "
                "Order remains on Binance -- orphan recovery will handle it. "
                "Error: %s",
                sell_order_id,
                lot.lot_id,
                db_exc,
            )
            return

        # Clear retry counters on successful placement
        await state.clear_keys(
            f"sell_retry_count:{lot.lot_id}",
            f"sell_retry_after:{lot.lot_id}",
        )

        self._touch_order()

        logger.info(
            "fixed_tp: placed TP sell order %s for lot %s at %.2f qty=%.8f",
            sell_order_id,
            lot.lot_id,
            target_price,
            sell_qty,
        )
