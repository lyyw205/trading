from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pyarrow.compute as pc
import pyarrow.parquet as pq
from sqlalchemy import update

from app.db.session import TradingSessionLocal  # results/status 저장용
from app.models.backtest_run import BacktestRun
from app.exchange.backtest_client import BacktestClient
from app.strategies.registry import BuyLogicRegistry, SellLogicRegistry
import app.strategies.buys  # noqa: F401 — trigger @register decorators
import app.strategies.sells  # noqa: F401
from backtest.mem_stores import (
    InMemoryAccountStateManager,
    InMemoryLotRepository,
    InMemoryOrderRepository,
    InMemoryStateStore,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Concurrency limiter: 1 at a time (module-level patch for trend.py requires serialization)
_semaphore = asyncio.Semaphore(1)

MAX_CANDLES = 100_000
EQUITY_SAMPLE_INTERVAL = 12  # every 12 candles = 1 hour for 5m candles


class IsolatedBacktestRunner:
    """In-memory backtest runner.

    All intermediate data (orders, lots, strategy state) lives in pure-dict
    in-memory stores — no DB transactions during candle replay.
    Only config loading (1x) and result saving (1x) touch the DB.
    """

    async def run(self, run_id: UUID) -> dict:
        """Execute a backtest for the given backtest_runs row."""
        async with _semaphore:
            return await self._run_internal(run_id)

    async def _run_internal(self, run_id: UUID) -> dict:
        # ----------------------------------------------------------------
        # 1. Load config from backtest_runs
        # ----------------------------------------------------------------
        async with TradingSessionLocal() as cfg_session:
            row = await cfg_session.get(BacktestRun, run_id)
            if not row:
                raise ValueError(f"BacktestRun {run_id} not found")

            symbol = row.symbol
            combo_configs = list(row.combos) if row.combos else []
            initial_usdt = float(row.initial_usdt)
            start_ts_ms = row.start_ts_ms
            end_ts_ms = row.end_ts_ms
            user_id = row.user_id

        if not combo_configs:
            await self._save_failure(run_id, "No combo configurations provided")
            return {"error": "No combo configurations"}

        # Mark as RUNNING
        await self._update_status(run_id, "RUNNING", started_at=datetime.utcnow())

        try:
            # ----------------------------------------------------------------
            # 2. Load candles from production (read-only)
            # ----------------------------------------------------------------
            candles = await self._load_candles(symbol, start_ts_ms, end_ts_ms)
            if not candles:
                await self._save_failure(run_id, "No candle data found for the given range")
                return {"error": "No candle data found"}

            if len(candles) > MAX_CANDLES:
                await self._save_failure(
                    run_id,
                    f"Too many candles: {len(candles)} (max {MAX_CANDLES})",
                )
                return {"error": f"Exceeds {MAX_CANDLES} candle limit"}

            logger.info(
                "Backtest %s: %d candles for %s [%d – %d], %d combos",
                run_id, len(candles), symbol, start_ts_ms, end_ts_ms, len(combo_configs),
            )

            # ----------------------------------------------------------------
            # 3. Create buy/sell logic instances for each combo
            # ----------------------------------------------------------------
            combos = []
            name_to_idx = {}
            for idx, cfg in enumerate(combo_configs):
                name = cfg["name"]
                try:
                    buy_logic = BuyLogicRegistry.create_instance(cfg["buy_logic_name"])
                    sell_logic = SellLogicRegistry.create_instance(cfg["sell_logic_name"])
                except KeyError as e:
                    logger.warning("Backtest: unknown logic '%s', skipping combo '%s'", e, name)
                    continue
                combos.append({
                    "name": name,
                    "buy_logic": buy_logic,
                    "sell_logic": sell_logic,
                    "buy_params": cfg.get("buy_params", {}),
                    "sell_params": cfg.get("sell_params", {}),
                    "reference_combo_name": cfg.get("reference_combo_name"),
                    "combo_id": uuid4(),
                })
                name_to_idx[name] = len(combos) - 1

            if not combos:
                await self._save_failure(run_id, "No valid combos found")
                return {"error": "No valid combos found"}

            # Resolve reference_combo_id from name
            for combo in combos:
                ref_name = combo.pop("reference_combo_name", None)
                if ref_name and ref_name in name_to_idx:
                    combo["reference_combo_id"] = combos[name_to_idx[ref_name]]["combo_id"]
                else:
                    combo["reference_combo_id"] = None

            # ----------------------------------------------------------------
            # 4. In-memory replay (DB 연결 불필요)
            # ----------------------------------------------------------------
            client = BacktestClient(symbol=symbol, initial_balance_usdt=initial_usdt)
            account_id = uuid4()
            equity_curve: list[dict] = []

            # Shared in-memory stores
            shared_backing: dict[str, str] = {}
            lot_repo = InMemoryLotRepository()
            order_repo = InMemoryOrderRepository()
            shared_asm = InMemoryAccountStateManager(account_id, shared_backing)

            # Module-level patch: trend.py의 lazy import가 InMemoryStateStore 사용
            import app.strategies.state_store as _ss_mod
            _original_ss = _ss_mod.StrategyStateStore
            _ss_mod.StrategyStateStore = (
                lambda aid, scope, session: InMemoryStateStore(aid, scope, shared_backing)
            )

            try:
                from app.strategies.base import RepositoryBundle, StrategyContext

                base_asset = symbol.replace("USDT", "")

                for i, candle in enumerate(candles):
                    price = float(candle["close"])
                    ts_ms = candle["ts_ms"]
                    sim_time = ts_ms / 1000.0  # ms → seconds for cooldown
                    candle_low = float(candle["low"])
                    candle_high = float(candle["high"])
                    client.set_candle(close=price, low=candle_low, high=candle_high, ts_ms=ts_ms)

                    for combo in combos:
                        combo_id = combo["combo_id"]
                        buy_logic = combo["buy_logic"]
                        sell_logic = combo["sell_logic"]
                        buy_logic._sim_time = sim_time
                        sell_logic._sim_time = sim_time

                        state = InMemoryStateStore(account_id, str(combo_id), shared_backing)
                        prefix = f"bt_{combo['name'][:8]}_"

                        repos = RepositoryBundle(
                            lot=lot_repo,
                            order=order_repo,
                            position=None,
                            price=None,
                        )

                        # Build buy params (inject reference_combo_id)
                        buy_params = {**buy_logic.default_params, **combo["buy_params"]}
                        if combo["reference_combo_id"]:
                            buy_params["_reference_combo_id"] = str(combo["reference_combo_id"])

                        buy_ctx = StrategyContext(
                            account_id=account_id,
                            symbol=symbol,
                            base_asset=base_asset,
                            quote_asset="USDT",
                            current_price=price,
                            params=buy_params,
                            client_order_prefix=prefix,
                        )

                        # 0. pre_tick
                        try:
                            await buy_logic.pre_tick(buy_ctx, state, client, repos, combo_id)
                        except Exception as exc:
                            logger.warning(
                                "Backtest pre_tick error (combo=%s, price=%.2f): %s",
                                combo["name"], price, exc,
                            )

                        # 1. sell
                        sell_params = {**sell_logic.default_params, **combo["sell_params"]}
                        sell_ctx = StrategyContext(
                            account_id=account_id,
                            symbol=symbol,
                            base_asset=base_asset,
                            quote_asset="USDT",
                            current_price=price,
                            params=sell_params,
                            client_order_prefix=prefix,
                        )
                        open_lots = await lot_repo.get_open_lots_by_combo(
                            account_id, symbol, combo_id,
                        )
                        try:
                            await sell_logic.tick(sell_ctx, state, client, shared_asm, repos, open_lots)
                        except Exception as exc:
                            logger.warning(
                                "Backtest sell tick error (combo=%s, price=%.2f): %s",
                                combo["name"], price, exc,
                            )

                        # 2. buy
                        try:
                            await buy_logic.tick(buy_ctx, state, client, shared_asm, repos, combo_id)
                        except Exception as exc:
                            logger.warning(
                                "Backtest buy tick error (combo=%s, price=%.2f): %s",
                                combo["name"], price, exc,
                            )

                    # Sample equity curve periodically
                    if i % EQUITY_SAMPLE_INTERVAL == 0:
                        eq_val = self._calc_equity(client, price, base_asset)
                        equity_curve.append({"ts_ms": ts_ms, "value": round(eq_val, 2)})

                # Final equity point
                final_price = float(candles[-1]["close"])
                final_equity = self._calc_equity(client, final_price, base_asset)
                if not equity_curve or equity_curve[-1]["ts_ms"] != candles[-1]["ts_ms"]:
                    equity_curve.append({
                        "ts_ms": candles[-1]["ts_ms"],
                        "value": round(final_equity, 2),
                    })

                # Collect results
                results = self._collect_results(
                    client, candles, initial_usdt, equity_curve, base_asset
                )

            finally:
                # Module-level patch 복원 필수
                _ss_mod.StrategyStateStore = _original_ss

            # ----------------------------------------------------------------
            # 5. Save results to backtest_runs (separate transaction)
            # ----------------------------------------------------------------
            await self._save_results(run_id, results)
            logger.info("Backtest %s completed: PnL %.2f%%", run_id, results["summary"]["pnl_pct"])
            return results

        except Exception as exc:
            logger.exception("Backtest %s failed: %s", run_id, exc)
            await self._save_failure(run_id, str(exc))
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_candles(
        self, symbol: str, start_ts_ms: int, end_ts_ms: int
    ) -> list[dict]:
        """Load candles from local Parquet file.

        Prefers 1m candles for higher resolution; falls back to 5m.
        Files expected at: data/{SYMBOL}_1m.parquet, data/{SYMBOL}_5m.parquet
        """
        for interval in ("1m", "5m"):
            parquet_path = DATA_DIR / f"{symbol}_{interval}.parquet"
            if not parquet_path.exists():
                logger.debug("Parquet not found: %s", parquet_path)
                continue

            table = pq.read_table(parquet_path)

            # Filter by time range
            mask = pc.and_(
                pc.greater_equal(table.column("ts_ms"), start_ts_ms),
                pc.less_equal(table.column("ts_ms"), end_ts_ms),
            )
            table = table.filter(mask)

            if len(table) == 0:
                logger.debug("No rows in range for %s", parquet_path)
                continue

            # Sort by ts_ms
            indices = pc.sort_indices(table, sort_keys=[("ts_ms", "ascending")])
            table = table.take(indices)

            logger.info(
                "Loaded %d candles from %s (%s)",
                len(table), parquet_path.name, interval,
            )

            return [
                {
                    "ts_ms": int(row["ts_ms"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]) if row["volume"] else 0.0,
                }
                for row in table.to_pylist()
            ]

        return []

    def _calc_equity(self, client: BacktestClient, price: float, base_asset: str) -> float:
        """Calculate total portfolio value at current price."""
        btc_bal = client._balances.get(base_asset, {"free": 0.0, "locked": 0.0})
        usdt_bal = client._balances.get("USDT", {"free": 0.0, "locked": 0.0})
        btc_total = btc_bal["free"] + btc_bal["locked"]
        usdt_total = usdt_bal["free"] + usdt_bal["locked"]
        return btc_total * price + usdt_total

    def _collect_results(
        self,
        client: BacktestClient,
        candles: list[dict],
        initial_usdt: float,
        equity_curve: list[dict],
        base_asset: str,
    ) -> dict:
        """Collect all result data from the backtest client."""
        final_price = candles[-1]["close"]
        final_value = self._calc_equity(client, final_price, base_asset)
        pnl_usdt = final_value - initial_usdt
        pnl_pct = (pnl_usdt / initial_usdt * 100) if initial_usdt > 0 else 0.0

        # Trade log from in-memory client
        trade_log = []
        for t in client._trades:
            trade_log.append({
                "ts_ms": t.get("time", 0),
                "side": t.get("side", ""),
                "price": t.get("price", "0"),
                "qty": t.get("qty", "0"),
                "quote_qty": t.get("quoteQty", "0"),
            })

        # Win/loss stats
        winning = 0
        losing = 0
        gross_profit = 0.0
        gross_loss = 0.0

        # Pair BUY/SELL trades for win/loss analysis
        buys: list[dict] = []
        for t in client._trades:
            if t.get("side") == "BUY" or t.get("isBuyer"):
                buys.append(t)
            else:
                if buys:
                    buy = buys.pop(0)
                    buy_price = float(buy["price"])
                    sell_price = float(t["price"])
                    qty = float(t["qty"])
                    profit = (sell_price - buy_price) * qty
                    if profit >= 0:
                        winning += 1
                        gross_profit += profit
                    else:
                        losing += 1
                        gross_loss += abs(profit)

        total_trades = len(client._trades)
        buy_trades = sum(1 for t in client._trades if t.get("side") == "BUY" or t.get("isBuyer"))
        sell_trades = total_trades - buy_trades
        total_round_trips = winning + losing
        win_rate = (winning / total_round_trips * 100) if total_round_trips > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        # Max drawdown from equity curve
        max_drawdown_pct = 0.0
        peak = 0.0
        for point in equity_curve:
            val = point["value"]
            if val > peak:
                peak = val
            if peak > 0:
                dd = (peak - val) / peak * 100
                if dd > max_drawdown_pct:
                    max_drawdown_pct = dd

        summary = {
            "final_value_usdt": round(final_value, 2),
            "pnl_usdt": round(pnl_usdt, 2),
            "pnl_pct": round(pnl_pct, 2),
            "total_trades": total_trades,
            "buy_trades": buy_trades,
            "sell_trades": sell_trades,
            "winning_trades": winning,
            "losing_trades": losing,
            "win_rate": round(win_rate, 2),
            "max_drawdown_pct": round(-max_drawdown_pct, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
        }

        return {
            "summary": summary,
            "trade_log": trade_log,
            "equity_curve": equity_curve,
        }

    async def _save_results(self, run_id: UUID, results: dict) -> None:
        """Persist results to backtest_runs table."""
        async with TradingSessionLocal() as session:
            stmt = (
                update(BacktestRun)
                .where(BacktestRun.id == run_id)
                .values(
                    status="COMPLETED",
                    result_summary=results["summary"],
                    trade_log=results["trade_log"],
                    equity_curve=results["equity_curve"],
                    completed_at=datetime.utcnow(),
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def _save_failure(self, run_id: UUID, error_msg: str) -> None:
        """Mark a backtest run as FAILED."""
        async with TradingSessionLocal() as session:
            stmt = (
                update(BacktestRun)
                .where(BacktestRun.id == run_id)
                .values(
                    status="FAILED",
                    error_message=error_msg[:1000],
                    completed_at=datetime.utcnow(),
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def _update_status(
        self, run_id: UUID, status: str, **kwargs
    ) -> None:
        """Update backtest run status."""
        async with TradingSessionLocal() as session:
            stmt = (
                update(BacktestRun)
                .where(BacktestRun.id == run_id)
                .values(status=status, **kwargs)
            )
            await session.execute(stmt)
            await session.commit()
