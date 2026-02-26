"""
CandleAggregator — periodic background job to compact old candles.

Retention policy:
  - 1m candles: kept for 7 days, then aggregated to 5m
  - 5m candles: kept for 30 days, then aggregated to 1h
  - 1h candles: kept for 90 days, then aggregated to 1d
"""
from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select, distinct

from app.db.session import TradingSessionLocal
from app.services.candle_store import aggregate_candles, delete_old_candles
from app.models.price_candle import PriceCandle1m

logger = logging.getLogger(__name__)

# Retention periods in milliseconds
_7_DAYS_MS = 7 * 24 * 60 * 60 * 1000
_30_DAYS_MS = 30 * 24 * 60 * 60 * 1000
_90_DAYS_MS = 90 * 24 * 60 * 60 * 1000

# Aggregation interval (6 hours)
_RUN_INTERVAL_SEC = 6 * 60 * 60

# Tiers: (source_interval, target_interval, retention_ms)
_TIERS = [
    ("1m", "5m", _7_DAYS_MS),
    ("5m", "1h", _30_DAYS_MS),
    ("1h", "1d", _90_DAYS_MS),
]


class CandleAggregator:
    """Periodic candle compaction job."""

    async def run_once(self) -> dict:
        """Execute one round of aggregation for all symbols.
        Returns a summary of work done."""
        now_ms = int(time.time() * 1000)
        summary = {}

        # Get all active symbols from 1m table
        async with TradingSessionLocal() as session:
            stmt = select(distinct(PriceCandle1m.symbol))
            result = await session.execute(stmt)
            symbols = [row[0] for row in result.all()]

        if not symbols:
            logger.debug("CandleAggregator: no symbols found, skipping")
            return summary

        for symbol in symbols:
            symbol_summary = {}
            for source_interval, target_interval, retention_ms in _TIERS:
                cutoff_ms = now_ms - retention_ms
                try:
                    # Single transaction: aggregate + delete
                    async with TradingSessionLocal() as session:
                        aggregated = await aggregate_candles(
                            symbol=symbol,
                            source_interval=source_interval,
                            target_interval=target_interval,
                            cutoff_ts_ms=cutoff_ms,
                            session=session,
                        )
                        deleted = 0
                        if aggregated > 0:
                            deleted = await delete_old_candles(
                                symbol=symbol,
                                interval=source_interval,
                                before_ts_ms=cutoff_ms,
                                session=session,
                            )
                        await session.commit()

                    if aggregated > 0 or deleted > 0:
                        symbol_summary[f"{source_interval}->{target_interval}"] = {
                            "aggregated": aggregated,
                            "deleted": deleted,
                        }
                        logger.info(
                            "CandleAggregator: %s %s->%s: aggregated=%d, deleted=%d",
                            symbol, source_interval, target_interval, aggregated, deleted,
                        )
                except Exception as e:
                    logger.error(
                        "CandleAggregator: %s %s->%s failed: %s",
                        symbol, source_interval, target_interval, e,
                    )
            if symbol_summary:
                summary[symbol] = symbol_summary

        return summary


async def run_aggregation_loop() -> None:
    """Background loop that runs CandleAggregator every 6 hours."""
    aggregator = CandleAggregator()
    logger.info("CandleAggregator: background loop started (interval: %ds)", _RUN_INTERVAL_SEC)

    # Wait 5 minutes after startup before first run
    await asyncio.sleep(300)

    while True:
        try:
            summary = await aggregator.run_once()
            if summary:
                logger.info("CandleAggregator: completed — %s", summary)
            else:
                logger.debug("CandleAggregator: no work to do")
        except asyncio.CancelledError:
            logger.info("CandleAggregator: background loop cancelled")
            return
        except Exception as e:
            logger.error("CandleAggregator: unexpected error: %s", e)

        try:
            await asyncio.sleep(_RUN_INTERVAL_SEC)
        except asyncio.CancelledError:
            logger.info("CandleAggregator: background loop cancelled during sleep")
            return
