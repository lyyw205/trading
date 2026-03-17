"""DailyReportService pure unit tests — no DB, mock-based."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.daily_report_service import DailyReportService, _to_float

KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# _to_float helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToFloat:
    def test_none_returns_zero(self):
        assert _to_float(None) == 0.0

    def test_decimal_rounds_to_4_places(self):
        assert _to_float(Decimal("3.14159")) == 3.1416

    def test_decimal_zero_returns_zero(self):
        assert _to_float(Decimal("0")) == 0.0

    def test_return_type_is_float(self):
        assert isinstance(_to_float(Decimal("1.5")), float)
        assert isinstance(_to_float(None), float)

    def test_rounds_half_up(self):
        # 1.23455 rounds to 1.2346 at 4dp
        assert _to_float(Decimal("1.23455")) == 1.2346

    def test_large_decimal(self):
        assert _to_float(Decimal("99999.9999")) == 99999.9999


# ---------------------------------------------------------------------------
# health_score 계산 로직
# (generate_report 내부 공식을 직접 재현하여 검증)
# ---------------------------------------------------------------------------

#  공식:
#   health = 100.0
#   health -= min(criticals_excl_cb * 10, 40)
#   health -= min(error_count * 2, 30)
#   health -= min(cb_trips * 20, 40)
#   health -= min(total_drifts * 5, 20)
#   health = max(health, 0.0)


def _calc_health(criticals_excl_cb: int, error_count: int, cb_trips: int, total_drifts: int = 0) -> float:
    score = 100.0
    score -= min(criticals_excl_cb * 10, 40)
    score -= min(error_count * 2, 30)
    score -= min(cb_trips * 20, 40)
    score -= min(total_drifts * 5, 20)
    return max(score, 0.0)


@pytest.mark.unit
class TestHealthScore:
    def test_all_zero_gives_100(self):
        assert _calc_health(0, 0, 0, 0) == 100.0

    def test_critical_5_capped_at_40(self):
        # 5 criticals → 5*10=50, capped at 40 → score = 60
        score = _calc_health(5, 0, 0, 0)
        assert score == 60.0

    def test_critical_4_not_capped(self):
        # 4 criticals → 4*10=40 (exactly at cap) → score = 60
        score = _calc_health(4, 0, 0, 0)
        assert score == 60.0

    def test_error_20_capped_at_30(self):
        # 20 errors → 20*2=40, capped at 30 → score = 70
        score = _calc_health(0, 20, 0, 0)
        assert score == 70.0

    def test_error_15_not_capped(self):
        # 15 errors → 15*2=30 (exactly at cap) → score = 70
        score = _calc_health(0, 15, 0, 0)
        assert score == 70.0

    def test_cb_3_capped_at_40(self):
        # 3 CB → 3*20=60, capped at 40 → score = 60
        score = _calc_health(0, 0, 3, 0)
        assert score == 60.0

    def test_cb_2_not_capped(self):
        # 2 CB → 2*20=40 (at cap) → score = 60
        score = _calc_health(0, 0, 2, 0)
        assert score == 60.0

    def test_drift_5_capped_at_20(self):
        # 5 drifts → 5*5=25, capped at 20 → score = 80
        score = _calc_health(0, 0, 0, 5)
        assert score == 80.0

    def test_drift_4_not_capped(self):
        # 4 drifts → 4*5=20 (at cap) → score = 80
        score = _calc_health(0, 0, 0, 4)
        assert score == 80.0

    def test_composite_critical2_error5_cb1(self):
        # CRITICAL 2 → 20, ERROR 5 → 10, CB 1 → 20, drift 0 → 0
        # score = 100 - 20 - 10 - 20 = 50
        score = _calc_health(2, 5, 1, 0)
        assert score == 50.0

    def test_floor_zero_never_negative(self):
        # Worst case: all caps hit → 100 - 40 - 30 - 40 - 20 = -30 → clamped to 0
        score = _calc_health(10, 30, 5, 10)
        assert score == 0.0

    def test_single_critical_deducts_10(self):
        score = _calc_health(1, 0, 0, 0)
        assert score == 90.0

    def test_single_error_deducts_2(self):
        score = _calc_health(0, 1, 0, 0)
        assert score == 98.0

    def test_single_cb_deducts_20(self):
        score = _calc_health(0, 0, 1, 0)
        assert score == 80.0

    def test_single_drift_deducts_5(self):
        score = _calc_health(0, 0, 0, 1)
        assert score == 95.0


# ---------------------------------------------------------------------------
# KST 타임존 경계 (period_start_utc / period_end_utc 계산 로직)
# ---------------------------------------------------------------------------

# 공식 (generate_report 내부):
#   period_end_utc = datetime(year, month, day, 15, 0, 0, tzinfo=UTC) - timedelta(days=1)
#   period_start_utc = period_end_utc - timedelta(days=1)
# 즉:
#   report_date KST 00:00 == (report_date-1) UTC 15:00 == period_end_utc
#   period_start_utc == (report_date-2) UTC 15:00


def _calc_periods(report_date: date):
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
    return period_start_utc, period_end_utc


@pytest.mark.unit
class TestKstBoundary:
    def test_2026_03_17_period_end(self):
        # report_date=2026-03-17 → period_end=2026-03-16T15:00:00Z
        _, period_end = _calc_periods(date(2026, 3, 17))
        assert period_end == datetime(2026, 3, 16, 15, 0, 0, tzinfo=UTC)

    def test_2026_03_17_period_start(self):
        # report_date=2026-03-17 → period_start=2026-03-15T15:00:00Z
        period_start, _ = _calc_periods(date(2026, 3, 17))
        assert period_start == datetime(2026, 3, 15, 15, 0, 0, tzinfo=UTC)

    def test_period_spans_exactly_24_hours(self):
        period_start, period_end = _calc_periods(date(2026, 3, 17))
        assert (period_end - period_start) == timedelta(hours=24)

    def test_month_boundary_2026_04_01_period_end(self):
        # report_date=2026-04-01 → period_end=2026-03-31T15:00:00Z
        _, period_end = _calc_periods(date(2026, 4, 1))
        assert period_end == datetime(2026, 3, 31, 15, 0, 0, tzinfo=UTC)

    def test_month_boundary_2026_04_01_period_start(self):
        # report_date=2026-04-01 → period_start=2026-03-30T15:00:00Z
        period_start, _ = _calc_periods(date(2026, 4, 1))
        assert period_start == datetime(2026, 3, 30, 15, 0, 0, tzinfo=UTC)

    def test_year_boundary_2026_01_01(self):
        # report_date=2026-01-01 → period_end=2025-12-31T15:00:00Z
        _, period_end = _calc_periods(date(2026, 1, 1))
        assert period_end == datetime(2025, 12, 31, 15, 0, 0, tzinfo=UTC)

    def test_all_periods_are_utc_aware(self):
        period_start, period_end = _calc_periods(date(2026, 3, 17))
        assert period_start.tzinfo is UTC
        assert period_end.tzinfo is UTC


# ---------------------------------------------------------------------------
# Helpers for building mock DailyReport
# ---------------------------------------------------------------------------


def _make_report(
    *,
    report_date: date = date(2026, 3, 17),
    health_score: float = 85.0,
    telegram_sent_at=None,
    discord_sent_at=None,
    summary: dict | None = None,
) -> MagicMock:
    report = MagicMock(
        spec=[
            "id",
            "report_date",
            "health_score",
            "summary",
            "telegram_sent_at",
            "discord_sent_at",
            "period_start",
            "period_end",
        ]
    )
    report.id = uuid.uuid4()
    report.report_date = report_date
    report.health_score = health_score
    report.telegram_sent_at = telegram_sent_at
    report.discord_sent_at = discord_sent_at
    report.period_start = datetime(2026, 3, 15, 15, 0, 0, tzinfo=UTC)
    report.period_end = datetime(2026, 3, 16, 15, 0, 0, tzinfo=UTC)
    report.summary = summary or {
        "total_errors": 2,
        "total_criticals": 0,
        "cb_events": 0,
        "top_error_modules": [{"module": "buy_logic", "count": 2}],
        "trading_totals": {
            "total_bought": 5,
            "total_closed": 3,
            "total_profit_usdt": 12.34,
            "total_fees_usdt": 0.50,
        },
        "accounts": [
            {
                "account_id": "acct-1",
                "name": "Main",
                "cb_status": "healthy",
                "net_profit_usdt": 12.34,
                "closed_lots": 3,
            }
        ],
        "reconciliation": {"total_drifts": 0, "total_auto_resolved": 0},
    }
    return report


# ---------------------------------------------------------------------------
# send_telegram_report
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendTelegramReport:
    @pytest.mark.asyncio
    async def test_already_sent_returns_false(self):
        service = DailyReportService()
        report = _make_report(telegram_sent_at=datetime.now(UTC))
        alert_service = AsyncMock()

        result = await service.send_telegram_report(report, alert_service)

        assert result is False
        alert_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_success_returns_true(self):
        service = DailyReportService()
        report = _make_report()
        alert_service = AsyncMock()
        alert_service.send = AsyncMock(return_value=True)

        with patch("app.services.daily_report_service.TradingSessionLocal") as mock_sl:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = AsyncMock(return_value=MagicMock())
            mock_session.commit = AsyncMock()
            mock_sl.return_value = mock_session

            result = await service.send_telegram_report(report, alert_service)

        assert result is True

    @pytest.mark.asyncio
    async def test_message_contains_health_score(self):
        service = DailyReportService()
        report = _make_report(health_score=72.0)
        alert_service = AsyncMock()
        alert_service.send = AsyncMock(return_value=False)

        await service.send_telegram_report(report, alert_service)

        call_args = alert_service.send.call_args
        msg = call_args[0][0]
        assert "72" in msg

    @pytest.mark.asyncio
    async def test_message_contains_profit_with_sign(self):
        service = DailyReportService()
        summary = {
            "total_errors": 0,
            "total_criticals": 0,
            "cb_events": 0,
            "top_error_modules": [],
            "trading_totals": {
                "total_bought": 1,
                "total_closed": 1,
                "total_profit_usdt": 5.50,
                "total_fees_usdt": 0.10,
            },
            "accounts": [],
            "reconciliation": {"total_drifts": 0, "total_auto_resolved": 0},
        }
        report = _make_report(summary=summary)
        alert_service = AsyncMock()
        alert_service.send = AsyncMock(return_value=False)

        await service.send_telegram_report(report, alert_service)

        msg = alert_service.send.call_args[0][0]
        # Positive profit should have + sign
        assert "+5.50" in msg

    @pytest.mark.asyncio
    async def test_message_account_icon_healthy(self):
        service = DailyReportService()
        report = _make_report()  # has "cb_status": "healthy"
        alert_service = AsyncMock()
        alert_service.send = AsyncMock(return_value=False)

        await service.send_telegram_report(report, alert_service)

        msg = alert_service.send.call_args[0][0]
        assert "🟢" in msg

    @pytest.mark.asyncio
    async def test_message_account_icon_disabled(self):
        service = DailyReportService()
        summary = {
            "total_errors": 0,
            "total_criticals": 0,
            "cb_events": 0,
            "top_error_modules": [],
            "trading_totals": {"total_bought": 0, "total_closed": 0, "total_profit_usdt": 0, "total_fees_usdt": 0},
            "accounts": [
                {
                    "account_id": "acct-2",
                    "name": "Secondary",
                    "cb_status": "disabled",
                    "net_profit_usdt": -1.0,
                    "closed_lots": 0,
                }
            ],
            "reconciliation": {"total_drifts": 0, "total_auto_resolved": 0},
        }
        report = _make_report(summary=summary)
        alert_service = AsyncMock()
        alert_service.send = AsyncMock(return_value=False)

        await service.send_telegram_report(report, alert_service)

        msg = alert_service.send.call_args[0][0]
        assert "🔴" in msg

    @pytest.mark.asyncio
    async def test_send_failure_returns_false(self):
        service = DailyReportService()
        report = _make_report()
        alert_service = AsyncMock()
        alert_service.send = AsyncMock(return_value=False)

        result = await service.send_telegram_report(report, alert_service)

        assert result is False


# ---------------------------------------------------------------------------
# send_discord_report
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendDiscordReport:
    @pytest.mark.asyncio
    async def test_already_sent_returns_false(self):
        service = DailyReportService()
        report = _make_report(discord_sent_at=datetime.now(UTC))

        result = await service.send_discord_report(report)

        assert result is False

    @pytest.mark.asyncio
    async def test_no_webhook_url_returns_false(self):
        service = DailyReportService()
        report = _make_report()

        mock_settings = MagicMock()
        mock_settings.discord_webhook_url = None

        with patch("app.config.get_settings", return_value=mock_settings):
            result = await service.send_discord_report(report)

        assert result is False

    @pytest.mark.asyncio
    async def test_empty_webhook_url_returns_false(self):
        service = DailyReportService()
        report = _make_report()

        mock_settings = MagicMock()
        mock_settings.discord_webhook_url = ""

        with patch("app.config.get_settings", return_value=mock_settings):
            result = await service.send_discord_report(report)

        assert result is False

    @pytest.mark.asyncio
    async def test_webhook_200_returns_true(self):
        service = DailyReportService()
        report = _make_report()

        mock_settings = MagicMock()
        mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test"

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.post = AsyncMock(return_value=mock_resp)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(return_value=MagicMock())
        mock_session.commit = AsyncMock()

        with (
            patch("app.config.get_settings", return_value=mock_settings),
            patch("app.services.daily_report_service.httpx.AsyncClient", return_value=mock_http_client),
            patch("app.services.daily_report_service.TradingSessionLocal", return_value=mock_session),
        ):
            result = await service.send_discord_report(report)

        assert result is True

    @pytest.mark.asyncio
    async def test_webhook_204_returns_true(self):
        service = DailyReportService()
        report = _make_report()

        mock_settings = MagicMock()
        mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test"

        mock_resp = MagicMock()
        mock_resp.status_code = 204

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.post = AsyncMock(return_value=mock_resp)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(return_value=MagicMock())
        mock_session.commit = AsyncMock()

        with (
            patch("app.config.get_settings", return_value=mock_settings),
            patch("app.services.daily_report_service.httpx.AsyncClient", return_value=mock_http_client),
            patch("app.services.daily_report_service.TradingSessionLocal", return_value=mock_session),
        ):
            result = await service.send_discord_report(report)

        assert result is True

    @pytest.mark.asyncio
    async def test_webhook_400_returns_false(self):
        service = DailyReportService()
        report = _make_report()

        mock_settings = MagicMock()
        mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test"

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.post = AsyncMock(return_value=mock_resp)

        with (
            patch("app.config.get_settings", return_value=mock_settings),
            patch("app.services.daily_report_service.httpx.AsyncClient", return_value=mock_http_client),
        ):
            result = await service.send_discord_report(report)

        assert result is False

    @pytest.mark.asyncio
    async def test_webhook_exception_returns_false(self):
        service = DailyReportService()
        report = _make_report()

        mock_settings = MagicMock()
        mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test"

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.post = AsyncMock(side_effect=Exception("network error"))

        with (
            patch("app.config.get_settings", return_value=mock_settings),
            patch("app.services.daily_report_service.httpx.AsyncClient", return_value=mock_http_client),
        ):
            result = await service.send_discord_report(report)

        assert result is False
