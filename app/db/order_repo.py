from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.fill import Fill


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert_order(self, account_id: UUID, order_data: dict) -> None:
        """PostgreSQL upsert from Binance API response."""
        stmt = (
            pg_insert(Order)
            .values(
                order_id=int(order_data["orderId"]),
                account_id=account_id,
                symbol=order_data["symbol"],
                side=order_data.get("side"),
                type=order_data.get("type"),
                status=order_data.get("status"),
                price=float(order_data["price"]) if order_data.get("price") is not None else None,
                orig_qty=float(order_data["origQty"]) if order_data.get("origQty") is not None else None,
                executed_qty=float(order_data["executedQty"]) if order_data.get("executedQty") is not None else None,
                cum_quote_qty=float(order_data["cummulativeQuoteQty"]) if order_data.get("cummulativeQuoteQty") is not None else None,
                client_order_id=order_data.get("clientOrderId"),
                update_time_ms=int(order_data["updateTime"]) if order_data.get("updateTime") is not None else None,
                raw_json=order_data,
            )
            .on_conflict_do_update(
                index_elements=["order_id", "account_id"],
                set_=dict(
                    symbol=order_data["symbol"],
                    side=order_data.get("side"),
                    type=order_data.get("type"),
                    status=order_data.get("status"),
                    price=float(order_data["price"]) if order_data.get("price") is not None else None,
                    orig_qty=float(order_data["origQty"]) if order_data.get("origQty") is not None else None,
                    executed_qty=float(order_data["executedQty"]) if order_data.get("executedQty") is not None else None,
                    cum_quote_qty=float(order_data["cummulativeQuoteQty"]) if order_data.get("cummulativeQuoteQty") is not None else None,
                    client_order_id=order_data.get("clientOrderId"),
                    update_time_ms=int(order_data["updateTime"]) if order_data.get("updateTime") is not None else None,
                    raw_json=order_data,
                ),
            )
        )
        await self._session.execute(stmt)

    async def get_order(self, account_id: UUID, order_id: int) -> Order | None:
        """Fetch a single order by composite PK."""
        return await self._session.get(Order, (order_id, account_id))

    async def get_recent_open_orders(
        self, account_id: UUID, limit: int = 50
    ) -> list[int]:
        """Return order_ids whose status is NEW or PARTIALLY_FILLED."""
        stmt = (
            select(Order.order_id)
            .where(
                Order.account_id == account_id,
                Order.status.in_(("NEW", "PARTIALLY_FILLED")),
            )
            .order_by(Order.update_time_ms.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def insert_fill(
        self, account_id: UUID, order_id: int, trade_data: dict
    ) -> None:
        """Insert from Binance trade response; ignore duplicates."""
        side = "BUY" if trade_data.get("isBuyer") else "SELL"
        stmt = (
            pg_insert(Fill)
            .values(
                trade_id=int(trade_data["id"]),
                account_id=account_id,
                order_id=order_id,
                symbol=trade_data["symbol"],
                side=side,
                price=float(trade_data["price"]) if trade_data.get("price") is not None else None,
                qty=float(trade_data["qty"]) if trade_data.get("qty") is not None else None,
                quote_qty=float(trade_data["quoteQty"]) if trade_data.get("quoteQty") is not None else None,
                commission=float(trade_data["commission"]) if trade_data.get("commission") is not None else None,
                commission_asset=trade_data.get("commissionAsset"),
                trade_time_ms=int(trade_data["time"]) if trade_data.get("time") is not None else None,
                raw_json=trade_data,
            )
            .on_conflict_do_nothing(index_elements=["trade_id", "account_id"])
        )
        await self._session.execute(stmt)
