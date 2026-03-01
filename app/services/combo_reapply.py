"""
Combo Reapply Service
튠 값 변경 시 미체결 주문을 취소하여 다음 사이클에서 새 파라미터로 재등록되도록 처리.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.db.lot_repo import LotRepository
from app.db.order_repo import OrderRepository
from app.exchange.binance_client import BinanceClient
from app.strategies.state_store import StrategyStateStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.account import TradingAccount
    from app.models.trading_combo import TradingCombo
    from app.utils.encryption import EncryptionManager

logger = logging.getLogger(__name__)

_PENDING_KEYS = (
    "pending_order_id",
    "pending_time_ms",
    "pending_bucket_usdt",
    "pending_kind",
    "pending_trigger_price",
)


async def reapply_combo_orders(
    account: TradingAccount,
    combo: TradingCombo,
    session: AsyncSession,
    encryption: EncryptionManager,
) -> dict:
    """
    콤보의 미체결 주문을 취소하여 다음 사이클에서 새 파라미터로 재등록되도록 한다.

    1. Pending buy order (strategy state) -> cancel + clear state
    2. Open lots with sell_order_id (TP orders) -> cancel + clear sell_order_id

    Returns summary dict with cancelled counts.
    """
    api_key = encryption.decrypt(account.api_key_encrypted)
    api_secret = encryption.decrypt(account.api_secret_encrypted)
    client = BinanceClient(api_key, api_secret, account.symbol)

    lot_repo = LotRepository(session)
    order_repo = OrderRepository(session)
    # State scope now includes symbol; process each combo symbol
    combo_symbols = combo.symbols if combo.symbols else [account.symbol]

    cancelled_buy = 0
    cancelled_sell = 0
    errors = []

    for symbol in combo_symbols:
        state = StrategyStateStore(account.id, f"{combo.id}:{symbol}", session)

        # 1. Cancel pending buy order for this symbol
        pending_order_id = await state.get("pending_order_id")
        if pending_order_id and str(pending_order_id).strip():
            order_id = int(pending_order_id)
            try:
                cancel_resp = await client.cancel_order(order_id, symbol)
                await order_repo.upsert_order(account.id, cancel_resp)
                cancelled_buy += 1
                logger.info(
                    "combo_reapply: cancelled pending buy order %s for combo %s symbol %s",
                    order_id, combo.id, symbol,
                )
            except Exception as exc:
                logger.warning(
                    "combo_reapply: cancel pending buy %s failed: %s", order_id, exc,
                )
                errors.append(f"buy order {order_id}: {exc}")

            await state.clear_keys(*_PENDING_KEYS)

        # 2. Cancel TP sell orders on open lots for this symbol
        open_lots = await lot_repo.get_open_lots_by_combo(
            account.id, symbol, combo.id,
        )
        for lot in open_lots:
            if not lot.sell_order_id:
                continue
            try:
                cancel_resp = await client.cancel_order(lot.sell_order_id, lot.symbol)
                await order_repo.upsert_order(account.id, cancel_resp)
                cancelled_sell += 1
                logger.info(
                    "combo_reapply: cancelled TP sell order %s for lot %s",
                    lot.sell_order_id, lot.lot_id,
                )
            except Exception as exc:
                logger.warning(
                    "combo_reapply: cancel TP sell %s failed: %s",
                    lot.sell_order_id, exc,
                )
                errors.append(f"sell order {lot.sell_order_id}: {exc}")

            await lot_repo.clear_sell_order(
                account_id=account.id, lot_id=lot.lot_id,
            )

    summary = {
        "cancelled_buy": cancelled_buy,
        "cancelled_sell": cancelled_sell,
        "errors": errors,
    }
    logger.info("combo_reapply: combo %s result: %s", combo.id, summary)
    return summary
