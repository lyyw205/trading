"""Daily operational report generation and scheduling."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, extract, func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.session import TradingSessionLocal, engine_trading
from app.models.account import TradingAccount
from app.models.daily_report import DailyReport
from app.models.lot import Lot
from app.models.persistent_log import PersistentLog
from app.models.position import Position
from app.models.reconciliation_log import ReconciliationLog

logger = logging.getLogger(__name__)

# KST = UTC+9
KST = timezone(timedelta(hours=9))

# Circuit breaker log pattern
CB_PATTERN = "Circuit breaker triggered"


def _dec(v: Decimal | None) -> float:
    """Decimal → float for JSON serialization."""
    if v is None:
        return 0.0
    return float(round(v, 4))


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

                # ============================================================
                # Section 1: Error/Log aggregation (existing)
                # ============================================================

                # 1a. Count by level
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

                # 1b. CB detection: level-independent count
                cb_result = await session.execute(
                    select(func.count())
                    .select_from(PersistentLog)
                    .where(*period_filter, PersistentLog.message.contains(CB_PATTERN))
                )
                cb_trips = cb_result.scalar() or 0

                criticals_excl_cb = max(critical_count - cb_trips, 0)

                # 1c. Top error modules (DB GROUP BY)
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

                # 1d. Per-account error stats (DB GROUP BY)
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
                acct_error_map: dict[str, dict] = {}
                for row in acct_stats_result.all():
                    key = str(row.account_id) if row.account_id else "system"
                    acct_error_map[key] = {
                        "errors": row.errors,
                        "criticals": row.criticals,
                        "cb_tripped": row.cb_cnt > 0,
                    }

                # ============================================================
                # Section 2: Hourly error distribution (시간대별)
                # ============================================================
                # Extract hour in KST (UTC + 9)
                kst_hour = extract("hour", PersistentLog.logged_at + text("INTERVAL '9 hours'"))
                hourly_result = await session.execute(
                    select(
                        kst_hour.label("hour_kst"),
                        func.count().label("cnt"),
                    )
                    .where(*period_filter)
                    .group_by("hour_kst")
                    .order_by("hour_kst")
                )
                hourly_distribution = {int(row.hour_kst): row.cnt for row in hourly_result.all()}
                # Fill missing hours with 0
                hourly_errors = [hourly_distribution.get(h, 0) for h in range(24)]

                # ============================================================
                # Section 3: Trading performance (거래 성과, 계정별)
                # ============================================================
                # 3a. Lots closed in period (sell_time within period)
                lot_period_filter = [
                    Lot.sell_time >= period_start_utc,
                    Lot.sell_time < period_end_utc,
                    Lot.status == "CLOSED",
                ]
                closed_lots_result = await session.execute(
                    select(
                        Lot.account_id,
                        func.count().label("closed_count"),
                        func.sum(Lot.net_profit_usdt).label("total_profit"),
                        func.sum(Lot.fee_usdt).label("total_fees"),
                        func.count().filter(Lot.net_profit_usdt > 0).label("win_count"),
                        func.avg(extract("epoch", Lot.sell_time) - extract("epoch", Lot.buy_time)).label(
                            "avg_hold_sec"
                        ),
                    )
                    .where(*lot_period_filter)
                    .group_by(Lot.account_id)
                )
                trading_perf: dict[str, dict] = {}
                for row in closed_lots_result.all():
                    acct_key = str(row.account_id)
                    closed = row.closed_count or 0
                    trading_perf[acct_key] = {
                        "closed_lots": closed,
                        "net_profit_usdt": _dec(row.total_profit),
                        "total_fees_usdt": _dec(row.total_fees),
                        "win_rate": round(row.win_count / closed, 4) if closed > 0 else 0.0,
                        "avg_hold_minutes": round((row.avg_hold_sec or 0) / 60, 1),
                    }

                # 3b. Lots opened (bought) in period
                bought_lots_result = await session.execute(
                    select(
                        Lot.account_id,
                        func.count().label("bought_count"),
                    )
                    .where(
                        Lot.buy_time >= period_start_utc,
                        Lot.buy_time < period_end_utc,
                    )
                    .group_by(Lot.account_id)
                )
                for row in bought_lots_result.all():
                    acct_key = str(row.account_id)
                    if acct_key not in trading_perf:
                        trading_perf[acct_key] = {
                            "closed_lots": 0,
                            "net_profit_usdt": 0.0,
                            "total_fees_usdt": 0.0,
                            "win_rate": 0.0,
                            "avg_hold_minutes": 0.0,
                        }
                    trading_perf[acct_key]["bought_lots"] = row.bought_count

                # Ensure bought_lots exists for all accounts
                for v in trading_perf.values():
                    v.setdefault("bought_lots", 0)

                # ============================================================
                # Section 4: Account status (계정 상태, 스냅샷)
                # ============================================================
                accounts_result = await session.execute(
                    select(
                        TradingAccount.id,
                        TradingAccount.name,
                        TradingAccount.is_active,
                        TradingAccount.circuit_breaker_failures,
                        TradingAccount.circuit_breaker_disabled_at,
                        TradingAccount.auto_recovery_attempts,
                        TradingAccount.last_auto_recovery_at,
                        TradingAccount.last_success_at,
                        TradingAccount.buy_pause_state,
                        TradingAccount.buy_pause_reason,
                        TradingAccount.buy_pause_since,
                        TradingAccount.consecutive_low_balance,
                        TradingAccount.pending_earnings_usdt,
                    )
                )
                account_status: list[dict] = []
                for row in accounts_result.all():
                    acct_key = str(row.id)
                    # CB status interpretation
                    if row.circuit_breaker_disabled_at:
                        if row.last_success_at and row.last_success_at > row.circuit_breaker_disabled_at:
                            cb_status = "recovered"
                        else:
                            cb_status = "disabled"
                    elif row.circuit_breaker_failures > 0:
                        cb_status = "degraded"
                    else:
                        cb_status = "healthy"

                    account_status.append(
                        {
                            "account_id": acct_key,
                            "name": row.name,
                            "is_active": row.is_active,
                            "cb_status": cb_status,
                            "cb_failures": row.circuit_breaker_failures,
                            "cb_disabled_at": row.circuit_breaker_disabled_at.isoformat()
                            if row.circuit_breaker_disabled_at
                            else None,
                            "auto_recovery_attempts": row.auto_recovery_attempts,
                            "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
                            "buy_pause_state": row.buy_pause_state,
                            "buy_pause_reason": row.buy_pause_reason,
                            "buy_pause_since": row.buy_pause_since.isoformat() if row.buy_pause_since else None,
                            "consecutive_low_balance": row.consecutive_low_balance,
                            "pending_earnings_usdt": _dec(row.pending_earnings_usdt),
                            # Merge error stats
                            **acct_error_map.get(acct_key, {"errors": 0, "criticals": 0, "cb_tripped": False}),
                            # Merge trading performance
                            **trading_perf.get(
                                acct_key,
                                {
                                    "closed_lots": 0,
                                    "bought_lots": 0,
                                    "net_profit_usdt": 0.0,
                                    "total_fees_usdt": 0.0,
                                    "win_rate": 0.0,
                                    "avg_hold_minutes": 0.0,
                                },
                            ),
                        }
                    )

                # ============================================================
                # Section 5: Portfolio (포트폴리오, 계정별 + 전체)
                # ============================================================
                # 5a. Current positions
                positions_result = await session.execute(
                    select(
                        Position.account_id,
                        Position.symbol,
                        Position.qty,
                        Position.cost_basis_usdt,
                        Position.avg_entry,
                    )
                )
                portfolio_by_account: dict[str, list[dict]] = {}
                total_cost_basis = Decimal("0")
                total_open_positions = 0
                for row in positions_result.all():
                    if row.qty <= 0:
                        continue
                    acct_key = str(row.account_id)
                    portfolio_by_account.setdefault(acct_key, []).append(
                        {
                            "symbol": row.symbol,
                            "qty": _dec(row.qty),
                            "cost_basis_usdt": _dec(row.cost_basis_usdt),
                            "avg_entry": _dec(row.avg_entry),
                        }
                    )
                    total_cost_basis += row.cost_basis_usdt
                    total_open_positions += 1

                # 5b. Open lots count per account
                open_lots_result = await session.execute(
                    select(
                        Lot.account_id,
                        func.count().label("open_lots"),
                    )
                    .where(Lot.status == "OPEN")
                    .group_by(Lot.account_id)
                )
                open_lots_map = {str(row.account_id): row.open_lots for row in open_lots_result.all()}

                portfolio_summary = {
                    "total_cost_basis_usdt": _dec(total_cost_basis),
                    "total_open_positions": total_open_positions,
                    "total_open_lots": sum(open_lots_map.values()),
                    "per_account": {
                        acct_key: {
                            "positions": positions,
                            "open_lots": open_lots_map.get(acct_key, 0),
                            "cost_basis_usdt": _dec(sum(Decimal(str(p["cost_basis_usdt"])) for p in positions)),
                        }
                        for acct_key, positions in portfolio_by_account.items()
                    },
                }

                # ============================================================
                # Section 6: Reconciliation (데이터 정합성)
                # ============================================================
                recon_period_filter = [
                    ReconciliationLog.checked_at >= period_start_utc,
                    ReconciliationLog.checked_at < period_end_utc,
                ]
                recon_result = await session.execute(
                    select(
                        ReconciliationLog.account_id,
                        func.count().label("total_checks"),
                        func.count().filter(ReconciliationLog.status == "drift_detected").label("drift_count"),
                        func.count().filter(ReconciliationLog.status == "error").label("error_count"),
                        func.count()
                        .filter(
                            ReconciliationLog.status == "drift_detected",
                            ReconciliationLog.auto_resolved.is_(True),
                        )
                        .label("auto_resolved"),
                        func.count()
                        .filter(
                            ReconciliationLog.position_diffs.isnot(None),
                            ReconciliationLog.status == "drift_detected",
                        )
                        .label("position_drift"),
                        func.count()
                        .filter(
                            ReconciliationLog.balance_diff.isnot(None),
                            ReconciliationLog.status == "drift_detected",
                        )
                        .label("balance_drift"),
                        func.count()
                        .filter(
                            ReconciliationLog.fill_gaps.isnot(None),
                            ReconciliationLog.status == "drift_detected",
                        )
                        .label("fill_gap"),
                    )
                    .where(*recon_period_filter)
                    .group_by(ReconciliationLog.account_id)
                )
                recon_by_account: list[dict] = []
                total_drifts = 0
                total_auto_resolved = 0
                for row in recon_result.all():
                    total_drifts += row.drift_count
                    total_auto_resolved += row.auto_resolved
                    recon_by_account.append(
                        {
                            "account_id": str(row.account_id),
                            "total_checks": row.total_checks,
                            "drift_count": row.drift_count,
                            "error_count": row.error_count,
                            "auto_resolved": row.auto_resolved,
                            "manual_needed": row.drift_count - row.auto_resolved,
                            "drift_types": {
                                "position": row.position_drift,
                                "balance": row.balance_drift,
                                "fill_gap": row.fill_gap,
                            },
                        }
                    )
                reconciliation = {
                    "total_drifts": total_drifts,
                    "total_auto_resolved": total_auto_resolved,
                    "total_manual_needed": total_drifts - total_auto_resolved,
                    "per_account": recon_by_account,
                }

                # ============================================================
                # Section 7: Server health (서버 안정성)
                # ============================================================
                server_health = await self._collect_server_health(session)

                # ============================================================
                # Health score calculation (운영 안정성 기반)
                # ============================================================
                health_score = 100.0
                health_score -= min(criticals_excl_cb * 10, 40)
                health_score -= min(error_count * 2, 30)
                health_score -= min(cb_trips * 20, 40)
                # Reconciliation drift penalty (new)
                health_score -= min(total_drifts * 5, 20)
                health_score = max(health_score, 0.0)

                # Build summary JSON
                summary = {
                    # Errors (기존)
                    "total_errors": error_count,
                    "total_criticals": critical_count,
                    "cb_events": cb_trips,
                    "top_error_modules": [{"module": mod, "count": cnt} for mod, cnt in top_modules],
                    # Hourly distribution (신규)
                    "hourly_errors": hourly_errors,
                    # Accounts (계정 상태 + 에러 + 거래 성과 통합)
                    "accounts": account_status,
                    # Trading totals (전체 거래 요약)
                    "trading_totals": {
                        "total_closed": sum(v.get("closed_lots", 0) for v in trading_perf.values()),
                        "total_bought": sum(v.get("bought_lots", 0) for v in trading_perf.values()),
                        "total_profit_usdt": _dec(
                            sum(Decimal(str(v.get("net_profit_usdt", 0))) for v in trading_perf.values())
                        ),
                        "total_fees_usdt": _dec(
                            sum(Decimal(str(v.get("total_fees_usdt", 0))) for v in trading_perf.values())
                        ),
                    },
                    # Portfolio (포트폴리오)
                    "portfolio": portfolio_summary,
                    # Reconciliation (정합성)
                    "reconciliation": reconciliation,
                    # Server health (서버 안정성)
                    "server_health": server_health,
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
                    "Generated daily report for %s: health=%.0f, errors=%d, criticals=%d, closed_lots=%d",
                    report_date,
                    health_score,
                    error_count,
                    critical_count,
                    summary["trading_totals"]["total_closed"],
                )
                return report
        except IntegrityError as exc:
            if "uq_daily_report_date" in str(exc.orig):
                # Race condition: another process already created the report
                logger.info("Report for %s created by another process, skipping", report_date)
                return None
            logger.error("Unexpected IntegrityError for report %s", report_date, exc_info=True)
            raise

    async def _collect_server_health(self, session) -> dict:
        """Collect server/DB health metrics at report generation time."""
        health: dict = {}

        # DB pool stats
        pool = engine_trading.pool
        health["db_pool"] = {
            "pool_size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "max_overflow": engine_trading.pool._max_overflow,
        }

        # DB connection test + server info
        try:
            db_version = await session.execute(text("SELECT version()"))
            health["db_version"] = db_version.scalar()

            db_size = await session.execute(text("SELECT pg_database_size(current_database())"))
            size_bytes = db_size.scalar() or 0
            health["db_size_mb"] = round(size_bytes / (1024 * 1024), 1)

            # Active connections
            conn_result = await session.execute(
                text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
            )
            health["db_active_connections"] = conn_result.scalar() or 0

            # Table sizes (top 5)
            table_sizes_result = await session.execute(
                text("""
                SELECT relname, pg_total_relation_size(c.oid) AS total_bytes
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                ORDER BY total_bytes DESC
                LIMIT 5
            """)
            )
            health["top_tables_mb"] = [
                {"table": row.relname, "size_mb": round(row.total_bytes / (1024 * 1024), 1)}
                for row in table_sizes_result.all()
            ]
        except Exception:
            logger.warning("Failed to collect some DB health metrics", exc_info=True)

        # Process uptime & memory (best-effort)
        try:
            import psutil

            proc = psutil.Process(os.getpid())
            mem_info = proc.memory_info()
            health["process"] = {
                "pid": proc.pid,
                "rss_mb": round(mem_info.rss / (1024 * 1024), 1),
                "cpu_percent": proc.cpu_percent(interval=0.1),
                "uptime_hours": round(
                    (datetime.now(UTC) - datetime.fromtimestamp(proc.create_time(), tz=UTC)).total_seconds() / 3600, 1
                ),
                "threads": proc.num_threads(),
            }
        except ImportError:
            health["process"] = {"note": "psutil not installed"}
        except Exception:
            logger.warning("Failed to collect process metrics", exc_info=True)

        return health

    async def send_telegram_report(self, report: DailyReport, alert_service) -> bool:
        """Send report summary to Telegram via AlertService."""
        if report.telegram_sent_at is not None:
            return False  # Already sent

        s = report.summary
        top_mods = ", ".join(f"{m['module']}({m['count']})" for m in s.get("top_error_modules", [])[:3])
        tt = s.get("trading_totals", {})
        recon = s.get("reconciliation", {})

        msg = (
            f"📊 [일일 리포트] {report.report_date}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"건강 점수: {report.health_score:.0f}/100\n"
            f"\n"
            f"🔴 장애 현황\n"
            f"  CRITICAL: {s.get('total_criticals', 0)}건 | ERROR: {s.get('total_errors', 0)}건\n"
            f"  CB 발동: {s.get('cb_events', 0)}회\n"
        )
        if top_mods:
            msg += f"  주요 모듈: {top_mods}\n"

        msg += (
            f"\n"
            f"💰 거래 성과\n"
            f"  매수: {tt.get('total_bought', 0)}건 | 매도: {tt.get('total_closed', 0)}건\n"
            f"  실현 손익: {tt.get('total_profit_usdt', 0):+.2f} USDT\n"
            f"  수수료: {tt.get('total_fees_usdt', 0):.2f} USDT\n"
        )

        # Per-account summary (compact)
        accounts = s.get("accounts", [])
        if accounts:
            msg += "\n📋 계정별 요약\n"
            for a in accounts:
                status_icon = (
                    "🟢" if a.get("cb_status") == "healthy" else "🔴" if a.get("cb_status") == "disabled" else "🟡"
                )
                name = a.get("name", a.get("account_id", "?")[:8])
                profit = a.get("net_profit_usdt", 0)
                msg += f"  {status_icon} {name}: {profit:+.2f} USDT ({a.get('closed_lots', 0)}건)\n"

        if recon.get("total_drifts", 0) > 0:
            msg += f"\n⚠️ 정합성\n  drift: {recon['total_drifts']}건 (자동해소: {recon.get('total_auto_resolved', 0)})\n"

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
