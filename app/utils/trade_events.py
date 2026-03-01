"""
Structured trading event logging.
Events are logged as JSON with standardized fields for grep/analysis.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("trading.events")


def log_event(event_type: str, **kwargs: Any) -> None:
    """
    Log a structured trading event.
    All events include the event_type field for filtering.
    Context vars (account_id, cycle_id, request_id) are auto-included
    by the StructuredFormatter.
    """
    extra_msg = " ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info("[%s] %s", event_type, extra_msg)


def cycle_start(account_id: str, cycle_id: str) -> None:
    log_event("CYCLE_START", account_id=account_id, cycle_id=cycle_id)


def cycle_end(account_id: str, cycle_id: str, duration_ms: float, buys: int = 0, sells: int = 0) -> None:
    log_event("CYCLE_END", account_id=account_id, cycle_id=cycle_id,
              duration_ms=f"{duration_ms:.1f}", buys=buys, sells=sells)


def price_fetched(symbol: str, price: float, source: str) -> None:
    log_event("PRICE_FETCHED", symbol=symbol, price=price, source=source)


def buy_decision(combo_id: str, should_buy: bool, pause_state: str, reason: str = "") -> None:
    log_event("BUY_DECISION", combo_id=combo_id, should_buy=should_buy,
              pause_state=pause_state, reason=reason)


def buy_placed(combo_id: str, order_id: int, qty: float, price: float) -> None:
    log_event("BUY_PLACED", combo_id=combo_id, order_id=order_id, qty=qty, price=price)


def sell_decision(combo_id: str, lot_id: str, action: str, tp_price: float, current_price: float) -> None:
    log_event("SELL_DECISION", combo_id=combo_id, lot_id=lot_id, action=action,
              tp_price=tp_price, current_price=current_price)


def sell_placed(combo_id: str, order_id: int, qty: float, price: float) -> None:
    log_event("SELL_PLACED", combo_id=combo_id, order_id=order_id, qty=qty, price=price)


def state_change(entity: str, old_state: str, new_state: str, trigger: str = "") -> None:
    log_event("STATE_CHANGE", entity=entity, old_state=old_state,
              new_state=new_state, trigger=trigger)
