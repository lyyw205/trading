"""Daily operational report generation and scheduling."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
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


def _to_float(value: Decimal | None) -> float:
    """Decimal → float for JSON serialization."""
    if value is None:
        return 0.0
    return float(round(value, 4))


def _sanitize_for_json(obj: object) -> object:
    """Recursively convert Decimal values to float for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(round(obj, 4))
    return obj


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
                # Section 1e: Detailed error/critical logs (상세 로그, 최대 100건)
                # ============================================================
                detail_logs_result = await session.execute(
                    select(
                        PersistentLog.logged_at,
                        PersistentLog.level,
                        PersistentLog.module,
                        PersistentLog.message,
                        PersistentLog.exception,
                        PersistentLog.account_id,
                    )
                    .where(
                        *period_filter,
                        PersistentLog.level.in_(["ERROR", "CRITICAL"]),
                    )
                    .order_by(PersistentLog.logged_at.desc())
                    .limit(100)
                )
                detail_logs: list[dict] = []
                for row in detail_logs_result.all():
                    detail_logs.append(
                        {
                            "logged_at": row.logged_at.isoformat(),
                            "level": row.level,
                            "module": row.module or "unknown",
                            "message": row.message[:500] if row.message else "",
                            "exception": row.exception[:1000] if row.exception else None,
                            "account_id": str(row.account_id) if row.account_id else None,
                        }
                    )

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
                        "net_profit_usdt": _to_float(row.total_profit),
                        "total_fees_usdt": _to_float(row.total_fees),
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
                # Section 3c: Detailed trade logs (개별 거래 내역)
                # ============================================================
                # Closed lots detail
                detail_closed_result = await session.execute(
                    select(
                        Lot.lot_id,
                        Lot.account_id,
                        Lot.symbol,
                        Lot.strategy_name,
                        Lot.buy_price,
                        Lot.buy_qty,
                        Lot.buy_time,
                        Lot.sell_price,
                        Lot.sell_time,
                        Lot.fee_usdt,
                        Lot.net_profit_usdt,
                    )
                    .where(*lot_period_filter)
                    .order_by(Lot.sell_time.desc())
                    .limit(200)
                )
                detail_closed_lots: list[dict] = []
                for row in detail_closed_result.all():
                    hold_sec = (row.sell_time - row.buy_time).total_seconds() if row.sell_time and row.buy_time else 0
                    detail_closed_lots.append(
                        {
                            "lot_id": row.lot_id,
                            "account_id": str(row.account_id),
                            "symbol": row.symbol,
                            "strategy": row.strategy_name,
                            "buy_price": _to_float(row.buy_price),
                            "buy_qty": _to_float(row.buy_qty),
                            "buy_time": row.buy_time.isoformat() if row.buy_time else None,
                            "sell_price": _to_float(row.sell_price),
                            "sell_time": row.sell_time.isoformat() if row.sell_time else None,
                            "fee_usdt": _to_float(row.fee_usdt),
                            "net_profit_usdt": _to_float(row.net_profit_usdt),
                            "hold_minutes": round(hold_sec / 60, 1),
                        }
                    )

                # Bought lots detail
                detail_bought_result = await session.execute(
                    select(
                        Lot.lot_id,
                        Lot.account_id,
                        Lot.symbol,
                        Lot.strategy_name,
                        Lot.buy_price,
                        Lot.buy_qty,
                        Lot.buy_time,
                        Lot.status,
                    )
                    .where(
                        Lot.buy_time >= period_start_utc,
                        Lot.buy_time < period_end_utc,
                    )
                    .order_by(Lot.buy_time.desc())
                    .limit(200)
                )
                detail_bought_lots: list[dict] = []
                for row in detail_bought_result.all():
                    detail_bought_lots.append(
                        {
                            "lot_id": row.lot_id,
                            "account_id": str(row.account_id),
                            "symbol": row.symbol,
                            "strategy": row.strategy_name,
                            "buy_price": _to_float(row.buy_price),
                            "buy_qty": _to_float(row.buy_qty),
                            "buy_time": row.buy_time.isoformat() if row.buy_time else None,
                            "status": row.status,
                        }
                    )

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
                            "pending_earnings_usdt": _to_float(row.pending_earnings_usdt),
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
                            "qty": _to_float(row.qty),
                            "cost_basis_usdt": _to_float(row.cost_basis_usdt),
                            "avg_entry": _to_float(row.avg_entry),
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
                    "total_cost_basis_usdt": _to_float(total_cost_basis),
                    "total_open_positions": total_open_positions,
                    "total_open_lots": sum(open_lots_map.values()),
                    "per_account": {
                        acct_key: {
                            "positions": positions,
                            "open_lots": open_lots_map.get(acct_key, 0),
                            "cost_basis_usdt": _to_float(sum(Decimal(str(p["cost_basis_usdt"])) for p in positions)),
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
                # Section 6b: Detailed reconciliation events (정합성 상세)
                # ============================================================
                detail_recon_result = await session.execute(
                    select(
                        ReconciliationLog.account_id,
                        ReconciliationLog.checked_at,
                        ReconciliationLog.status,
                        ReconciliationLog.position_diffs,
                        ReconciliationLog.balance_diff,
                        ReconciliationLog.fill_gaps,
                        ReconciliationLog.auto_resolved,
                    )
                    .where(
                        *recon_period_filter,
                        ReconciliationLog.status.in_(["drift_detected", "error"]),
                    )
                    .order_by(ReconciliationLog.checked_at.desc())
                    .limit(50)
                )
                detail_recon_events: list[dict] = []
                for row in detail_recon_result.all():
                    detail_recon_events.append(
                        {
                            "account_id": str(row.account_id),
                            "checked_at": row.checked_at.isoformat(),
                            "status": row.status,
                            "position_diffs": row.position_diffs,
                            "balance_diff": row.balance_diff,
                            "fill_gaps": row.fill_gaps,
                            "auto_resolved": row.auto_resolved,
                        }
                    )

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
                        "total_profit_usdt": _to_float(
                            sum(Decimal(str(v.get("net_profit_usdt", 0))) for v in trading_perf.values())
                        ),
                        "total_fees_usdt": _to_float(
                            sum(Decimal(str(v.get("total_fees_usdt", 0))) for v in trading_perf.values())
                        ),
                    },
                    # Portfolio (포트폴리오)
                    "portfolio": portfolio_summary,
                    # Reconciliation (정합성)
                    "reconciliation": reconciliation,
                    # Server health (서버 안정성)
                    "server_health": server_health,
                    # ---- Detail sections (상세 데이터, 분석용) ----
                    "detail_logs": detail_logs,
                    "detail_closed_lots": detail_closed_lots,
                    "detail_bought_lots": detail_bought_lots,
                    "detail_recon_events": detail_recon_events,
                }

                # Create report
                report = DailyReport(
                    report_date=report_date,
                    generated_at=datetime.now(UTC),
                    period_start=period_start_utc,
                    period_end=period_end_utc,
                    health_score=health_score,
                    summary=_sanitize_for_json(summary),
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

        summary = report.summary
        top_mods = ", ".join(f"{m['module']}({m['count']})" for m in summary.get("top_error_modules", [])[:3])
        trading_totals = summary.get("trading_totals", {})
        recon = summary.get("reconciliation", {})

        msg = (
            f"📊 [일일 리포트] {report.report_date}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"건강 점수: {report.health_score:.0f}/100\n"
            f"\n"
            f"🔴 장애 현황\n"
            f"  CRITICAL: {summary.get('total_criticals', 0)}건 | ERROR: {summary.get('total_errors', 0)}건\n"
            f"  CB 발동: {summary.get('cb_events', 0)}회\n"
        )
        if top_mods:
            msg += f"  주요 모듈: {top_mods}\n"

        msg += (
            f"\n"
            f"💰 거래 성과\n"
            f"  매수: {trading_totals.get('total_bought', 0)}건 | 매도: {trading_totals.get('total_closed', 0)}건\n"
            f"  실현 손익: {trading_totals.get('total_profit_usdt', 0):+.2f} USDT\n"
            f"  수수료: {trading_totals.get('total_fees_usdt', 0):.2f} USDT\n"
        )

        # Per-account summary (compact)
        accounts = summary.get("accounts", [])
        if accounts:
            msg += "\n📋 계정별 요약\n"
            for acct in accounts:
                status_icon = (
                    "🟢"
                    if acct.get("cb_status") == "healthy"
                    else "🔴"
                    if acct.get("cb_status") == "disabled"
                    else "🟡"
                )
                name = acct.get("name", acct.get("account_id", "?")[:8])
                profit = acct.get("net_profit_usdt", 0)
                msg += f"  {status_icon} {name}: {profit:+.2f} USDT ({acct.get('closed_lots', 0)}건)\n"

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

    async def send_discord_report(self, report: DailyReport) -> bool:
        """Send report summary to Discord via webhook embed."""
        if report.discord_sent_at is not None:
            return False  # Already sent

        from app.config import get_settings

        webhook_url = get_settings().discord_webhook_url
        if not webhook_url:
            return False

        summary = report.summary
        trading_totals = summary.get("trading_totals", {})
        recon = summary.get("reconciliation", {})
        score = float(report.health_score)

        # Color: green >= 80, yellow >= 50, red < 50
        if score >= 80:
            color = 0x2ECC71  # green
        elif score >= 50:
            color = 0xF1C40F  # yellow
        else:
            color = 0xE74C3C  # red

        # Build fields
        fields = []

        # Health score
        fields.append({"name": "건강 점수", "value": f"**{score:.0f}** / 100", "inline": True})

        # Trading performance
        profit = trading_totals.get("total_profit_usdt", 0)
        profit_sign = "+" if profit >= 0 else ""
        fields.append(
            {
                "name": "💰 거래 성과",
                "value": (
                    f"매수 **{trading_totals.get('total_bought', 0)}**건 | 매도 **{trading_totals.get('total_closed', 0)}**건\n"
                    f"손익 **{profit_sign}{profit:.2f}** USDT\n"
                    f"수수료 {trading_totals.get('total_fees_usdt', 0):.2f} USDT"
                ),
                "inline": True,
            }
        )

        # Errors
        fields.append(
            {
                "name": "🔴 장애",
                "value": (
                    f"CRITICAL **{summary.get('total_criticals', 0)}**건\n"
                    f"ERROR **{summary.get('total_errors', 0)}**건\n"
                    f"CB 발동 **{summary.get('cb_events', 0)}**회"
                ),
                "inline": True,
            }
        )

        # Per-account summary
        accounts = summary.get("accounts", [])
        if accounts:
            lines = []
            for acct in accounts:
                icon = (
                    "🟢"
                    if acct.get("cb_status") == "healthy"
                    else "🔴"
                    if acct.get("cb_status") == "disabled"
                    else "🟡"
                )
                name = acct.get("name", acct.get("account_id", "?")[:8])
                profit = acct.get("net_profit_usdt", 0)
                profit_sign = "+" if profit >= 0 else ""
                lines.append(f"{icon} **{name}**: {profit_sign}{profit:.2f} USDT ({acct.get('closed_lots', 0)}건)")
            fields.append({"name": "📋 계정별 요약", "value": "\n".join(lines), "inline": False})

        # Reconciliation
        if recon.get("total_drifts", 0) > 0:
            fields.append(
                {
                    "name": "⚠️ 정합성",
                    "value": f"drift **{recon['total_drifts']}**건 (자동해소: {recon.get('total_auto_resolved', 0)})",
                    "inline": True,
                }
            )

        # Top error modules
        top_mods = summary.get("top_error_modules", [])[:3]
        if top_mods:
            mod_lines = ", ".join(f"`{m['module']}`({m['count']})" for m in top_mods)
            fields.append({"name": "주요 에러 모듈", "value": mod_lines, "inline": True})

        embed = {
            "title": f"📊 일일 리포트 — {report.report_date}",
            "color": color,
            "fields": fields,
            "footer": {"text": f"기간: {report.period_start:%m/%d %H:%M} ~ {report.period_end:%m/%d %H:%M} KST"},
        }

        payload = {"embeds": [embed]}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(webhook_url, json=payload)
                if resp.status_code in (200, 204):
                    async with TradingSessionLocal() as session:
                        report_in_db = await session.get(DailyReport, report.id)
                        if report_in_db:
                            report_in_db.discord_sent_at = datetime.now(UTC)
                            await session.commit()
                    return True
                logger.warning("Discord webhook returned %d: %s", resp.status_code, resp.text[:200])
                return False
        except Exception:
            logger.warning("Discord webhook send failed", exc_info=True)
            return False

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
                    if existing_report:
                        # Report exists, try sending if not sent
                        if alert_service:
                            await service.send_telegram_report(existing_report, alert_service)
                        await service.send_discord_report(existing_report)
                    elif not existing_report:
                        # All retries failed — always log, optionally alert
                        logger.error("Daily report generation failed for %s after all retries", today_kst)
                        if alert_service:
                            await alert_service.send_high(f"⚠️ 일일 리포트 생성 실패: {today_kst} (3회 재시도 실패)")
            elif report:
                if alert_service:
                    sent = await service.send_telegram_report(report, alert_service)
                    if not sent:
                        logger.warning("Daily report for %s generated but Telegram send failed/skipped", today_kst)
                discord_sent = await service.send_discord_report(report)
                if not discord_sent:
                    logger.warning("Daily report for %s generated but Discord send failed/skipped", today_kst)

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
