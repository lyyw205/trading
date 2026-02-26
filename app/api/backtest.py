from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, delete, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_trading_session
from app.dependencies import require_admin
from app.models.backtest_run import BacktestRun
from app.schemas.backtest import (
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestStatusResponse,
    BacktestReportResponse,
    BacktestConfigOut,
    BacktestSummaryOut,
    BacktestListItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.post("/run", response_model=BacktestRunResponse)
async def run_backtest(
    req: BacktestRunRequest,
    request: Request,
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Start a backtest asynchronously. Returns run_id for polling."""
    if not req.combos:
        raise HTTPException(status_code=400, detail="At least one combo is required")

    # Serialize combo configs for storage
    combos_data = [c.model_dump() for c in req.combos]

    run = BacktestRun(
        user_id=UUID(user["id"]),
        symbol=req.symbol,
        combos=combos_data,
        initial_usdt=req.initial_usdt,
        start_ts_ms=req.start_ts_ms,
        end_ts_ms=req.end_ts_ms,
        status="PENDING",
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    run_id = run.id

    # Launch backtest in background
    from backtest.isolated_runner import IsolatedBacktestRunner

    runner = IsolatedBacktestRunner()
    asyncio.create_task(runner.run(run_id))

    return BacktestRunResponse(id=run_id, status="PENDING")


@router.get("/{run_id}/status", response_model=BacktestStatusResponse)
async def get_backtest_status(
    run_id: UUID,
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Poll backtest status."""
    run = await session.get(BacktestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return BacktestStatusResponse(
        id=run.id,
        status=run.status,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.get("/{run_id}/report")
async def get_backtest_report(
    run_id: UUID,
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Get full backtest report with candles."""
    run = await session.get(BacktestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Backtest not found")

    if run.status != "COMPLETED":
        raise HTTPException(
            status_code=400,
            detail=f"Backtest is {run.status}, not COMPLETED",
        )

    # Load candles for the chart
    from app.models.price_candle import PriceCandle5m, PriceCandle1m

    # Try 1m candles first (higher resolution)
    stmt_1m = (
        select(
            PriceCandle1m.ts_ms,
            PriceCandle1m.open,
            PriceCandle1m.high,
            PriceCandle1m.low,
            PriceCandle1m.close,
            PriceCandle1m.volume,
        )
        .where(
            PriceCandle1m.symbol == run.symbol,
            PriceCandle1m.ts_ms >= run.start_ts_ms,
            PriceCandle1m.ts_ms <= run.end_ts_ms,
        )
        .order_by(PriceCandle1m.ts_ms)
    )
    candle_result = await session.execute(stmt_1m)
    candle_rows = candle_result.all()

    # Fall back to 5m if no 1m data
    if not candle_rows:
        stmt_5m = (
            select(
                PriceCandle5m.ts_ms,
                PriceCandle5m.open,
                PriceCandle5m.high,
                PriceCandle5m.low,
                PriceCandle5m.close,
                PriceCandle5m.volume,
            )
            .where(
                PriceCandle5m.symbol == run.symbol,
                PriceCandle5m.ts_ms >= run.start_ts_ms,
                PriceCandle5m.ts_ms <= run.end_ts_ms,
            )
            .order_by(PriceCandle5m.ts_ms)
        )
        candle_result = await session.execute(stmt_5m)
        candle_rows = candle_result.all()

    candles = [
        {
            "time": int(r.ts_ms / 1000),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume) if r.volume else 0.0,
        }
        for r in candle_rows
    ]

    config = BacktestConfigOut(
        symbol=run.symbol,
        combos=run.combos,
        strategies=list(run.strategies) if run.strategies else None,
        strategy_params=run.strategy_params,
        initial_usdt=float(run.initial_usdt),
        start_ts_ms=run.start_ts_ms,
        end_ts_ms=run.end_ts_ms,
    )

    summary = None
    if run.result_summary:
        summary = BacktestSummaryOut(**run.result_summary)

    return BacktestReportResponse(
        id=run.id,
        config=config,
        summary=summary,
        trade_log=run.trade_log,
        equity_curve=run.equity_curve,
        candles=candles,
    )


@router.get("/list", response_model=list[BacktestListItem])
async def list_backtests(
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """List all backtest runs (newest first)."""
    stmt = select(BacktestRun).order_by(desc(BacktestRun.created_at)).limit(50)
    result = await session.execute(stmt)
    runs = result.scalars().all()

    items = []
    for r in runs:
        pnl_pct = None
        if r.result_summary and "pnl_pct" in r.result_summary:
            pnl_pct = r.result_summary["pnl_pct"]
        items.append(
            BacktestListItem(
                id=r.id,
                symbol=r.symbol,
                combos=r.combos,
                strategies=list(r.strategies) if r.strategies else None,
                initial_usdt=float(r.initial_usdt),
                start_ts_ms=r.start_ts_ms,
                end_ts_ms=r.end_ts_ms,
                status=r.status,
                pnl_pct=pnl_pct,
                created_at=r.created_at,
            )
        )
    return items


@router.delete("/{run_id}")
async def delete_backtest(
    run_id: UUID,
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Delete a backtest run."""
    run = await session.get(BacktestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Backtest not found")
    if run.status == "RUNNING":
        raise HTTPException(status_code=400, detail="Cannot delete a running backtest")
    await session.delete(run)
    await session.commit()
    return {"status": "deleted", "id": str(run_id)}
