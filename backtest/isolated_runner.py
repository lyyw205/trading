from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.db.session import TradingSessionLocal, engine_trading
from app.models.price_candle import PriceCandle5m
from app.models.backtest_run import BacktestRun
from app.models.user import UserProfile
from app.models.account import TradingAccount
from app.exchange.backtest_client import BacktestClient
from app.strategies.registry import BuyLogicRegistry, SellLogicRegistry
from app.strategies.state_store import StrategyStateStore
from app.services.account_state_manager import AccountStateManager

logger = logging.getLogger(__name__)

# Concurrency limiter: max 3 simultaneous backtests
_semaphore = asyncio.Semaphore(3)

MAX_CANDLES = 100_000
EQUITY_SAMPLE_INTERVAL = 12  # every 12 candles = 1 hour for 5m candles


class IsolatedBacktestRunner:
    """Transaction-rollback isolated backtest runner.

    All intermediate data (synthetic accounts, orders, lots, strategy state)
    is written inside a single PG transaction that is rolled back at the end.
    Only the final results are persisted to backtest_runs in a separate transaction.
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
        await self._update_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))

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
            # 4. Isolated transaction: replay candles
            # ----------------------------------------------------------------
            client = BacktestClient(symbol=symbol, initial_balance_usdt=initial_usdt)
            account_id = uuid4()
            equity_curve: list[dict] = []

            # Use a raw connection to control transaction manually
            async with engine_trading.connect() as conn:
                # Begin transaction (auto-started by asyncpg)
                async with conn.begin() as txn:
                    # Create a session bound to this connection
                    from sqlalchemy.ext.asyncio import AsyncSession
                    session = AsyncSession(bind=conn, expire_on_commit=False)

                    # Insert synthetic user + account for FK satisfaction
                    await self._insert_synthetic_rows(
                        session, user_id, account_id, symbol
                    )
                    await session.flush()

                    # Replay candles
                    from app.db.lot_repo import LotRepository
                    from app.db.order_repo import OrderRepository
                    from app.db.position_repo import PositionRepository
                    from app.strategies.base import RepositoryBundle, StrategyContext

                    base_asset = symbol.replace("USDT", "")

                    for i, candle in enumerate(candles):
                        price = float(candle["close"])
                        ts_ms = candle["ts_ms"]
                        client.set_price(price)

                        for combo in combos:
                            combo_id = combo["combo_id"]
                            buy_logic = combo["buy_logic"]
                            sell_logic = combo["sell_logic"]

                            state = StrategyStateStore(account_id, str(combo_id), session)
                            shared = AccountStateManager(account_id, session)
                            prefix = f"bt_{combo['name'][:8]}_"

                            repos = RepositoryBundle(
                                lot=LotRepository(session),
                                order=OrderRepository(session),
                                position=PositionRepository(session),
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
                            open_lots = await LotRepository(session).get_open_lots_by_combo(
                                account_id, symbol, combo_id,
                            )
                            try:
                                await sell_logic.tick(sell_ctx, state, client, shared, repos, open_lots)
                            except Exception as exc:
                                logger.warning(
                                    "Backtest sell tick error (combo=%s, price=%.2f): %s",
                                    combo["name"], price, exc,
                                )

                            # 2. buy
                            try:
                                await buy_logic.tick(buy_ctx, state, client, shared, repos, combo_id)
                            except Exception as exc:
                                logger.warning(
                                    "Backtest buy tick error (combo=%s, price=%.2f): %s",
                                    combo["name"], price, exc,
                                )

                        await session.flush()

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

                    # Collect results before rollback
                    results = self._collect_results(
                        client, candles, initial_usdt, equity_curve, base_asset
                    )

                    await session.close()

                    # ROLLBACK — all intermediate data vanishes
                    await txn.rollback()

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
        """Load candles from production DB as plain dicts (read-only)."""
        async with TradingSessionLocal() as session:
            stmt = (
                select(
                    PriceCandle5m.ts_ms,
                    PriceCandle5m.open,
                    PriceCandle5m.high,
                    PriceCandle5m.low,
                    PriceCandle5m.close,
                )
                .where(
                    PriceCandle5m.symbol == symbol,
                    PriceCandle5m.ts_ms >= start_ts_ms,
                    PriceCandle5m.ts_ms <= end_ts_ms,
                )
                .order_by(PriceCandle5m.ts_ms)
            )
            result = await session.execute(stmt)
            rows = result.all()
            return [
                {
                    "ts_ms": r.ts_ms,
                    "open": float(r.open),
                    "high": float(r.high),
                    "low": float(r.low),
                    "close": float(r.close),
                }
                for r in rows
            ]

    async def _insert_synthetic_rows(
        self, session, user_id: UUID, account_id: UUID, symbol: str
    ) -> None:
        """Insert synthetic user_profile + trading_account for FK satisfaction."""
        synthetic_user_id = uuid4()
        session.add(UserProfile(
            id=synthetic_user_id,
            email=f"backtest-{account_id}@synthetic.local",
            role="user",
        ))
        await session.flush()

        session.add(TradingAccount(
            id=account_id,
            owner_id=synthetic_user_id,
            name=f"Backtest {account_id}",
            symbol=symbol,
            base_asset=symbol.replace("USDT", ""),
            quote_asset="USDT",
            api_key_encrypted="backtest",
            api_secret_encrypted="backtest",
        ))
        await session.flush()

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
                    completed_at=datetime.now(timezone.utc),
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
                    completed_at=datetime.now(timezone.utc),
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
