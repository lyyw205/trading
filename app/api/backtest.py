from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, load_only

from app.db.session import get_trading_session
from app.dependencies import require_admin
from app.models.backtest_run import BacktestRun
from app.schemas.backtest import (
    BacktestConfigOut,
    BacktestListItem,
    BacktestPresetOut,
    BacktestPresetSaveRequest,
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

MAX_CONCURRENT_BACKTESTS = 3
_running_tasks: dict[str, asyncio.Task] = {}


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

    # Check concurrent backtest limit (DB-based, crash-safe)
    from sqlalchemy import func as sa_func

    active_count_stmt = (
        select(sa_func.count()).select_from(BacktestRun).where(BacktestRun.status.in_(["RUNNING", "PENDING"]))
    )
    active_count = (await session.execute(active_count_stmt)).scalar_one()
    if active_count >= MAX_CONCURRENT_BACKTESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {MAX_CONCURRENT_BACKTESTS} concurrent backtests. Try again later.",
        )

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

    # Clean up finished tasks from tracking dict
    active = {k: t for k, t in _running_tasks.items() if not t.done()}
    _running_tasks.clear()
    _running_tasks.update(active)
    if len(active) >= MAX_CONCURRENT_BACKTESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {MAX_CONCURRENT_BACKTESTS} concurrent backtests. Try again later.",
        )

    # Launch backtest in background with tracking
    from backtest.isolated_runner import IsolatedBacktestRunner

    runner = IsolatedBacktestRunner()
    task = asyncio.create_task(runner.run(run_id))
    _running_tasks[str(run_id)] = task

    def _on_backtest_done(t: asyncio.Task, _rid: UUID = run_id) -> None:
        _running_tasks.pop(str(_rid), None)
        if not t.cancelled() and t.exception():
            logger.error("Backtest %s failed: %s", _rid, t.exception())

    task.add_done_callback(_on_backtest_done)

    return BacktestRunResponse(id=run_id, status="PENDING")


@router.get("/{run_id}/status", response_model=BacktestStatusResponse)
async def get_backtest_status(
    run_id: UUID,
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """Poll backtest status."""
    run = await session.get(
        BacktestRun,
        run_id,
        options=[load_only(
            BacktestRun.id,
            BacktestRun.status,
            BacktestRun.error_message,
            BacktestRun.started_at,
            BacktestRun.completed_at,
        )],
    )
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

    # Load candles from local Parquet for the chart (offload blocking I/O)
    from backtest.isolated_runner import DATA_DIR

    _MAX_CHART_CANDLES = 2000

    def _load_candles() -> tuple[list[dict], int]:
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

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
            rows = [
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
            # Downsample OHLCV if too many candles for the chart
            if len(rows) > _MAX_CHART_CANDLES:
                n = len(rows)
                bucket_size = -(-n // _MAX_CHART_CANDLES)  # ceil division
                bucket_interval = bucket_size * 60  # seconds
                aggregated: dict[int, dict] = {}
                for row in rows:
                    key = (row["time"] // bucket_interval) * bucket_interval
                    if key not in aggregated:
                        aggregated[key] = {
                            "time": key,
                            "open": row["open"],
                            "high": row["high"],
                            "low": row["low"],
                            "close": row["close"],
                            "volume": row.get("volume", 0.0),
                        }
                    else:
                        b = aggregated[key]
                        if row["high"] > b["high"]:
                            b["high"] = row["high"]
                        if row["low"] < b["low"]:
                            b["low"] = row["low"]
                        b["close"] = row["close"]
                        b["volume"] = b["volume"] + row.get("volume", 0.0)
                return sorted(aggregated.values(), key=lambda x: x["time"]), bucket_interval
            return rows, 60
        return [], 60

    candles, candle_interval_sec = await asyncio.to_thread(_load_candles)

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

    # Auto-save to JSON on first view (offload blocking I/O to thread)
    await asyncio.to_thread(_auto_save, run, candles)  # type: ignore[arg-type]

    # Check pinned status from saved file
    def _is_pinned() -> bool:
        path = _saved_path(str(run.id))
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return bool(data.get("pinned", False))
        except Exception:
            return False

    pinned = await asyncio.to_thread(_is_pinned)

    return BacktestReportResponse(
        id=run.id,
        config=config,
        summary=summary,
        trade_log=run.trade_log,
        equity_curve=run.equity_curve,
        candles=candles,
        candle_interval_sec=candle_interval_sec,
        pinned=pinned,
    )


@router.get("/list", response_model=list[BacktestListItem])
async def list_backtests(
    user: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """List all backtest runs (newest first)."""
    stmt = (
        select(BacktestRun)
        .options(defer(BacktestRun.trade_log), defer(BacktestRun.equity_curve))
        .order_by(desc(BacktestRun.created_at))
        .limit(50)
    )
    result = await session.execute(stmt)
    runs = result.scalars().all()

    # Collect pinned status from JSON files (offload blocking I/O to thread)
    def _get_pinned_ids() -> set[str]:
        pinned = set()
        for f in SAVED_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("pinned"):
                    pinned.add(f.stem)
            except Exception:
                pass
        return pinned

    pinned_ids = await asyncio.to_thread(_get_pinned_ids)

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
    run = await session.get(
        BacktestRun,
        run_id,
        options=[load_only(BacktestRun.id, BacktestRun.status)],
    )
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
        "saved_at": datetime.now(UTC).isoformat(),
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
    run_id: UUID,
    user: dict = Depends(require_admin),
):
    """Toggle pinned status of a saved backtest report."""
    path = _saved_path(str(run_id))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Saved report not found")

    def _toggle() -> bool:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["pinned"] = not data.get("pinned", False)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data["pinned"]

    pinned = await asyncio.to_thread(_toggle)
    return {"status": "ok", "id": run_id, "pinned": pinned}


@router.delete("/saved/{run_id}")
async def delete_saved_report(
    run_id: UUID,
    user: dict = Depends(require_admin),
):
    """Delete a saved backtest JSON file."""
    path = _saved_path(str(run_id))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Saved report not found")
    await asyncio.to_thread(path.unlink)
    return {"status": "deleted", "id": run_id}


# ---- Combo Presets (JSON file storage) ----

PRESET_PATH = SAVED_DIR / "backtest_presets.json"


def _load_presets() -> dict:
    if not PRESET_PATH.exists():
        return {}
    try:
        return json.loads(PRESET_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_presets(data: dict) -> None:
    PRESET_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@router.get("/presets", response_model=list[BacktestPresetOut])
async def list_presets(
    user: dict = Depends(require_admin),
):
    """List all saved combo presets."""
    presets = await asyncio.to_thread(_load_presets)
    return [
        BacktestPresetOut(name=name, combos=p["combos"], saved_at=p.get("saved_at", ""))
        for name, p in presets.items()
    ]


@router.post("/presets")
async def save_preset(
    req: BacktestPresetSaveRequest,
    user: dict = Depends(require_admin),
):
    """Save combo configuration as a named preset (overwrites on name collision)."""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Preset name is required")
    if not req.combos:
        raise HTTPException(status_code=400, detail="At least one combo is required")

    def _save() -> None:
        presets = _load_presets()
        presets[req.name.strip()] = {
            "combos": [c.model_dump() for c in req.combos],
            "saved_at": datetime.now(UTC).isoformat(),
        }
        _save_presets(presets)

    await asyncio.to_thread(_save)
    return {"status": "ok", "name": req.name.strip()}


@router.delete("/presets/{name}")
async def delete_preset(
    name: str,
    user: dict = Depends(require_admin),
):
    """Delete a saved combo preset."""

    def _delete() -> bool:
        presets = _load_presets()
        if name not in presets:
            return False
        del presets[name]
        _save_presets(presets)
        return True

    found = await asyncio.to_thread(_delete)
    if not found:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"status": "deleted", "name": name}
