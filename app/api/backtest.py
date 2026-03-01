from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_trading_session
from app.dependencies import require_admin
from app.models.backtest_run import BacktestRun
from app.schemas.backtest import (
    BacktestConfigOut,
    BacktestListItem,
    BacktestReportResponse,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestStatusResponse,
    BacktestSummaryOut,
)

logger = logging.getLogger(__name__)

SAVED_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backtests"
SAVED_DIR.mkdir(parents=True, exist_ok=True)

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

    # Load candles from local Parquet for the chart
    from backtest.isolated_runner import DATA_DIR

    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    candles = []
    for interval in ("1m", "5m"):
        parquet_path = DATA_DIR / f"{run.symbol}_{interval}.parquet"
        if not parquet_path.exists():
            continue
        table = pq.read_table(parquet_path)
        mask = pc.and_(
            pc.greater_equal(table.column("ts_ms"), run.start_ts_ms),
            pc.less_equal(table.column("ts_ms"), run.end_ts_ms),
        )
        table = table.filter(mask)
        if len(table) == 0:
            continue
        indices = pc.sort_indices(table, sort_keys=[("ts_ms", "ascending")])
        table = table.take(indices)
        candles = [
            {
                "time": int(row["ts_ms"] / 1000),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]) if row["volume"] else 0.0,
            }
            for row in table.to_pylist()
        ]
        break

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

    # Auto-save to JSON on first view
    _auto_save(run, candles)

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

    # Collect pinned status from JSON files
    pinned_ids = set()
    for f in SAVED_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("pinned"):
                pinned_ids.add(f.stem)
        except Exception:
            pass

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
                pinned=str(r.id) in pinned_ids,
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


# ---- Auto-save & pin (JSON file storage) ----


def _saved_path(run_id: str) -> Path:
    return SAVED_DIR / f"{run_id}.json"


def _auto_save(run: BacktestRun, candles: list[dict]) -> None:
    """Auto-save report to JSON on first view (idempotent)."""
    path = _saved_path(str(run.id))
    if path.exists():
        return
    data = {
        "id": str(run.id),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "pinned": False,
        "config": {
            "symbol": run.symbol,
            "combos": run.combos,
            "strategies": list(run.strategies) if run.strategies else None,
            "strategy_params": run.strategy_params,
            "initial_usdt": float(run.initial_usdt),
            "start_ts_ms": run.start_ts_ms,
            "end_ts_ms": run.end_ts_ms,
        },
        "summary": run.result_summary,
        "trade_log": run.trade_log,
        "equity_curve": run.equity_curve,
        "candles": candles,
    }
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        logger.warning("Failed to auto-save backtest %s", run.id)


@router.post("/{run_id}/pin")
async def toggle_pin(
    run_id: str,
    user: dict = Depends(require_admin),
):
    """Toggle pinned status of a saved backtest report."""
    path = _saved_path(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Saved report not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["pinned"] = not data.get("pinned", False)
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"status": "ok", "id": run_id, "pinned": data["pinned"]}


@router.delete("/saved/{run_id}")
async def delete_saved_report(
    run_id: str,
    user: dict = Depends(require_admin),
):
    """Delete a saved backtest JSON file."""
    path = _saved_path(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Saved report not found")
    path.unlink()
    return {"status": "deleted", "id": run_id}
