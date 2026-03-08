"""Daily operational reports API endpoints."""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_trading_session
from app.dependencies import limiter, require_admin
from app.models.daily_report import DailyReport

router = APIRouter(prefix="/api/reports", tags=["reports"])
logger = logging.getLogger(__name__)


@router.get("/daily")
@limiter.limit("60/minute")
async def list_daily_reports(
    request: Request,
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
    limit: Annotated[int, Query(ge=1, le=90)] = 30,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> list[dict]:
    """List recent daily reports."""
    result = await session.execute(
        select(DailyReport).order_by(DailyReport.report_date.desc()).offset(offset).limit(limit)
    )
    reports = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "report_date": r.report_date.isoformat(),
            "generated_at": r.generated_at.isoformat(),
            "health_score": r.health_score,
            "summary": r.summary,
            "telegram_sent_at": r.telegram_sent_at.isoformat() if r.telegram_sent_at else None,
        }
        for r in reports
    ]


@router.get("/daily/{report_date}")
@limiter.limit("60/minute")
async def get_daily_report(
    report_date: date,
    request: Request,
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
) -> dict:
    """Get a specific daily report by date."""
    result = await session.execute(
        select(DailyReport).where(DailyReport.report_date == report_date)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"No report for {report_date}")

    return {
        "id": str(report.id),
        "report_date": report.report_date.isoformat(),
        "generated_at": report.generated_at.isoformat(),
        "period_start": report.period_start.isoformat(),
        "period_end": report.period_end.isoformat(),
        "health_score": report.health_score,
        "summary": report.summary,
        "telegram_sent_at": report.telegram_sent_at.isoformat() if report.telegram_sent_at else None,
    }


@router.post("/daily/{report_date}/generate")
@limiter.limit("10/minute")
async def generate_daily_report(
    report_date: date,
    request: Request,
    _admin: dict = Depends(require_admin),
) -> dict:
    """Manually trigger report generation for a specific date."""
    from app.services.daily_report_service import DailyReportService

    service = DailyReportService()
    report = await service.generate_report(report_date)

    if report is None:
        return {"status": "skipped", "message": f"Report for {report_date} already exists"}

    # Try sending Telegram
    alert_service = getattr(request.app.state, "alert_service", None)
    if alert_service:
        await service.send_telegram_report(report, alert_service)

    return {
        "status": "created",
        "report_date": report.report_date.isoformat(),
        "health_score": report.health_score,
    }
