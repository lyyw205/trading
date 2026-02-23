from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_current_user

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    symbol: str = "BTCUSDT"
    start_ts_ms: int
    end_ts_ms: int
    initial_usdt: float = 10000.0
    strategies: list[str] = ["lot_stacking"]
    strategy_params: dict[str, dict] = {}


@router.post("/run")
async def run_backtest(
    req: BacktestRequest,
    user: dict = Depends(get_current_user),
):
    """Run a backtest over stored historical candle data.

    Returns PnL summary and final balance breakdown.
    """
    from backtest.runner import BacktestRunner

    runner = BacktestRunner(
        symbol=req.symbol,
        initial_usdt=req.initial_usdt,
        strategy_names=req.strategies,
        strategy_params=req.strategy_params,
    )
    result = await runner.run(req.start_ts_ms, req.end_ts_ms)
    return result
