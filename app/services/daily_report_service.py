"""Daily operational report generation and scheduling."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.session import TradingSessionLocal
from app.models.daily_report import DailyReport
from app.models.persistent_log import PersistentLog

logger = logging.getLogger(__name__)

# KST = UTC+9
KST = timezone(timedelta(hours=9))

# Circuit breaker log pattern
CB_PATTERN = "Circuit breaker triggered"


class DailyReportService:
    """Generates daily operational reports from persistent logs."""

    # Retry config for report generation
    RETRY_DELAYS = [60, 300, 900]  # seconds

    # Retention
    LOG_RETENTION_DAYS = 30
    REPORT_RETENTION_DAYS = 365
    CLEANUP_BATCH_SIZE = 1000

    async def generate_report(self, report_date: date) -> DailyReport | None:
        """Generate report for given date (KST day boundary).

        Period: (report_date - 1) KST 00:00 ~ report_date KST 00:00
        Which is: (report_date - 2) UTC 15:00 ~ (report_date - 1) UTC 15:00

        All aggregation is done in DB to avoid loading logs into memory.
        """
        # report_date KST 00:00 = (report_date - 1 day) UTC 15:00
        period_end_utc = datetime(
            report_date.year,
            report_date.month,
            report_date.day,
            15,
            0,
            0,
            tzinfo=UTC,
        ) - timedelta(days=1)
        period_start_utc = period_end_utc - timedelta(days=1)

        period_filter = [
            PersistentLog.logged_at >= period_start_utc,
            PersistentLog.logged_at < period_end_utc,
        ]

        try:
            async with TradingSessionLocal() as session:
                # Check if report already exists
                existing = await session.execute(select(DailyReport).where(DailyReport.report_date == report_date))
                if existing.scalar_one_or_none():
                    logger.info("Report for %s already exists, skipping", report_date)
                    return None

                # --- DB aggregate queries (no full log load) ---

                # 1. Count by level
                level_stats = await session.execute(
                    select(
                        PersistentLog.level,
                        func.count().label("cnt"),
                    )
                    .where(*period_filter)
                    .group_by(PersistentLog.level)
                )
                error_count = 0
                critical_count = 0
                for row in level_stats.all():
                    if row.level == "ERROR":
                        error_count = row.cnt
                    elif row.level == "CRITICAL":
                        critical_count = row.cnt

                # CB detection: level-independent count
                cb_result = await session.execute(
                    select(func.count())
                    .select_from(PersistentLog)
                    .where(*period_filter, PersistentLog.message.contains(CB_PATTERN))
                )
                cb_trips = cb_result.scalar() or 0

                criticals_excl_cb = max(critical_count - cb_trips, 0)

                # 2. Top error modules (DB GROUP BY)
                top_modules_result = await session.execute(
                    select(
                        func.coalesce(PersistentLog.module, "unknown").label("mod"),
                        func.count().label("cnt"),
                    )
                    .where(*period_filter)
                    .group_by("mod")
                    .order_by(func.count().desc())
                    .limit(5)
                )
                top_modules = [(row.mod, row.cnt) for row in top_modules_result.all()]

                # 3. Per-account stats (DB GROUP BY, CB detection level-independent)
                acct_stats_result = await session.execute(
                    select(
                        PersistentLog.account_id,
                        func.count().filter(PersistentLog.level == "ERROR").label("errors"),
                        func.count().filter(PersistentLog.level == "CRITICAL").label("criticals"),
                        func.count()
                        .filter(
                            PersistentLog.message.contains(CB_PATTERN),
                        )
                        .label("cb_cnt"),
                    )
                    .where(*period_filter)
                    .group_by(PersistentLog.account_id)
                )
                account_stats_list = [
                    {
                        "account_id": str(row.account_id) if row.account_id else "system",
                        "errors": row.errors,
                        "criticals": row.criticals,
                        "cb_tripped": row.cb_cnt > 0,
                    }
                    for row in acct_stats_result.all()
                ]

                # Health score calculation
                health_score = 100.0
                health_score -= min(criticals_excl_cb * 10, 40)
                health_score -= min(error_count * 2, 30)
                health_score -= min(cb_trips * 20, 40)
                health_score = max(health_score, 0.0)

                # Build summary JSON
                summary = {
                    "total_errors": error_count,
                    "total_criticals": critical_count,
                    "cb_events": cb_trips,
                    "top_error_modules": [{"module": mod, "count": cnt} for mod, cnt in top_modules],
                    "accounts": account_stats_list,
                }

                # Create report
                report = DailyReport(
                    report_date=report_date,
                    generated_at=datetime.now(UTC),
                    period_start=period_start_utc,
                    period_end=period_end_utc,
                    health_score=health_score,
                    summary=summary,
                )
                session.add(report)
                await session.commit()
                await session.refresh(report)

                logger.info(
                    "Generated daily report for %s: health=%.0f, errors=%d, criticals=%d",
                    report_date,
                    health_score,
                    error_count,
                    critical_count,
                )
                return report
        except IntegrityError as exc:
            if "uq_daily_report_date" in str(exc.orig):
                # Race condition: another process already created the report
                logger.info("Report for %s created by another process, skipping", report_date)
                return None
            logger.error("Unexpected IntegrityError for report %s", report_date, exc_info=True)
            raise

    async def send_telegram_report(self, report: DailyReport, alert_service) -> bool:
        """Send report summary to Telegram via AlertService."""
        if report.telegram_sent_at is not None:
            return False  # Already sent

        s = report.summary
        top_mods = ", ".join(f"{m['module']}({m['count']})" for m in s.get("top_error_modules", [])[:3])

        msg = (
            f"📊 [일일 리포트] {report.report_date}\n"
            f"건강 점수: {report.health_score:.0f}/100\n"
            f"CRITICAL: {s.get('total_criticals', 0)}건 | ERROR: {s.get('total_errors', 0)}건\n"
            f"CB 발동: {s.get('cb_events', 0)}회\n"
        )
        if top_mods:
            msg += f"주요 모듈: {top_mods}\n"

        from app.services.alert_service import AlertSeverity

        sent = await alert_service.send(msg, AlertSeverity.HIGH)

        if sent:
            async with TradingSessionLocal() as session:
                report_in_db = await session.get(DailyReport, report.id)
                if report_in_db:
                    report_in_db.telegram_sent_at = datetime.now(UTC)
                    await session.commit()

        return sent

    async def cleanup_old_data(self) -> None:
        """Delete logs older than 30 days and reports older than 365 days in batches."""
        now = datetime.now(UTC)
        log_cutoff = now - timedelta(days=self.LOG_RETENTION_DAYS)
        report_cutoff = now - timedelta(days=self.REPORT_RETENTION_DAYS)

        # Batch delete old logs using subquery (1-RTT per batch)
        total_deleted = 0
        async with TradingSessionLocal() as session:
            while True:
                sub = (
                    select(PersistentLog.id).where(PersistentLog.logged_at < log_cutoff).limit(self.CLEANUP_BATCH_SIZE)
                )
                result = await session.execute(delete(PersistentLog).where(PersistentLog.id.in_(sub)))
                await session.commit()
                if result.rowcount == 0:
                    break
                total_deleted += result.rowcount

        if total_deleted:
            logger.info("Cleaned up %d old persistent logs", total_deleted)

        # Delete old reports (few records, no batching needed)
        async with TradingSessionLocal() as session:
            result = await session.execute(delete(DailyReport).where(DailyReport.report_date < report_cutoff.date()))
            await session.commit()
            if result.rowcount:
                logger.info("Cleaned up %d old daily reports", result.rowcount)


async def run_daily_report_loop(alert_service=None) -> None:
    """Background scheduler for daily report generation.

    - On startup: check if today's report exists, generate if not
    - Then sleep until next KST midnight + 5 min buffer
    - On failure: retry 3 times with backoff (60s, 300s, 900s)
    """
    service = DailyReportService()

    # Initial report check
    await asyncio.sleep(30)  # Wait for app to fully initialize

    while True:
        try:
            # Calculate today's report date (KST)
            now_kst = datetime.now(KST)
            today_kst = now_kst.date()

            # Try to generate today's report
            report = None
            for attempt, delay in enumerate(service.RETRY_DELAYS):
                try:
                    report = await service.generate_report(today_kst)
                    break
                except Exception:
                    logger.error(
                        "Daily report generation failed (attempt %d/%d)",
                        attempt + 1,
                        len(service.RETRY_DELAYS),
                        exc_info=True,
                    )
                    if attempt < len(service.RETRY_DELAYS) - 1:
                        await asyncio.sleep(delay)

            if report is None:
                # Check if it was because it already existed (not a failure)
                async with TradingSessionLocal() as session:
                    existing = await session.execute(select(DailyReport).where(DailyReport.report_date == today_kst))
                    existing_report = existing.scalar_one_or_none()
                    if existing_report and alert_service:
                        # Report exists, try sending telegram if not sent
                        await service.send_telegram_report(existing_report, alert_service)
                    elif not existing_report:
                        # All retries failed — always log, optionally alert
                        logger.error("Daily report generation failed for %s after all retries", today_kst)
                        if alert_service:
                            await alert_service.send_high(f"⚠️ 일일 리포트 생성 실패: {today_kst} (3회 재시도 실패)")
            elif report and alert_service:
                sent = await service.send_telegram_report(report, alert_service)
                if not sent:
                    logger.warning("Daily report for %s generated but Telegram send failed/skipped", today_kst)

            # Run cleanup
            await service.cleanup_old_data()

            # Sleep until next KST midnight + 5 min buffer
            now_utc = datetime.now(UTC)
            tomorrow_kst = today_kst + timedelta(days=1)
            # KST midnight = UTC 15:00 of previous day
            # KST 00:05 on tomorrow = UTC 15:05 on today (KST = UTC+9)
            next_run_utc = datetime(
                tomorrow_kst.year,
                tomorrow_kst.month,
                tomorrow_kst.day,
                15,
                5,
                0,
                tzinfo=UTC,
            ) - timedelta(days=1)
            sleep_seconds = (next_run_utc - now_utc).total_seconds()
            if sleep_seconds < 0:
                sleep_seconds = 60  # If we somehow missed it, retry in 1 min

            logger.info("Next daily report at %s (sleeping %.0f seconds)", next_run_utc, sleep_seconds)
            await asyncio.sleep(sleep_seconds)

        except asyncio.CancelledError:
            logger.info("Daily report loop cancelled")
            break
        except Exception:
            logger.error("Unexpected error in daily report loop", exc_info=True)
            await asyncio.sleep(300)  # Wait 5 min on unexpected errors
