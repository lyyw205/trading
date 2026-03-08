"""Async log persister: drains PersistLogHandler queue and batch-inserts to DB."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue

from sqlalchemy import insert

from app.db.session import TradingSessionLocal
from app.models.persistent_log import PersistentLog

logger = logging.getLogger(__name__)

# Prevent this module's own logs from being persisted (recursion guard)
logger.propagate = True


class LogPersister:
    """Consumes log entries from PersistLogHandler's queue and writes to DB in batches."""

    FLUSH_INTERVAL = 5.0  # seconds between scheduled flushes
    BATCH_THRESHOLD = 50  # early flush when queue has this many entries
    MAX_BATCH_SIZE = 200  # max entries per INSERT
    MAX_RETRY = 3  # max re-queue attempts per entry before drop

    def __init__(self, log_queue: queue.Queue[dict]) -> None:
        self._queue = log_queue
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background flush loop."""
        self._running = True
        self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Graceful shutdown: drain remaining entries then stop."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        # Final drain
        await self._flush_batch()

    async def _flush_loop(self) -> None:
        """Main loop: flush every FLUSH_INTERVAL or when batch threshold is reached."""
        while self._running:
            try:
                # Check threshold more frequently for faster flush under load
                for _ in range(int(self.FLUSH_INTERVAL)):
                    await asyncio.sleep(1.0)
                    if not self._running:
                        break
                    if self._queue.qsize() >= self.BATCH_THRESHOLD:
                        break
                await self._flush_batch()
            except asyncio.CancelledError:
                break
            except Exception:
                # Never let the flush loop die from a transient error
                logger.warning("Log persister flush error", exc_info=True)
                await asyncio.sleep(1)

    async def _flush_batch(self) -> None:
        """Drain queue and bulk-insert entries."""
        entries: list[dict] = []
        try:
            while len(entries) < self.MAX_BATCH_SIZE:
                entries.append(self._queue.get_nowait())
        except queue.Empty:
            pass

        if not entries:
            return

        try:
            # Strip internal retry metadata before DB insert
            clean_entries = [{k: v for k, v in e.items() if k != "_retry"} for e in entries]
            async with TradingSessionLocal() as session:
                await session.execute(insert(PersistentLog), clean_entries)
                await session.commit()
        except Exception:
            logger.warning("Failed to persist %d log entries, re-queuing", len(entries), exc_info=True)
            # Re-queue entries with retry count; drop after MAX_RETRY attempts
            requeued = 0
            dropped = 0
            for entry in entries:
                retry = entry.get("_retry", 0) + 1
                if retry > self.MAX_RETRY:
                    dropped += 1
                    continue
                entry["_retry"] = retry
                try:
                    self._queue.put_nowait(entry)
                    requeued += 1
                except queue.Full:
                    dropped += len(entries) - requeued - dropped
                    break
            if dropped:
                logger.error(
                    "Dropped %d/%d log entries (max retry or queue full)",
                    dropped,
                    len(entries),
                )
