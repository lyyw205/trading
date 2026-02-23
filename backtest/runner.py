from __future__ import annotations
import asyncio
import logging
from uuid import uuid4

from sqlalchemy import select

from app.db.session import TradingSessionLocal
from app.models.price_candle import PriceCandle5m
from app.exchange.backtest_client import BacktestClient
from app.strategies.registry import StrategyRegistry
from app.strategies.state_store import StrategyStateStore
from app.services.account_state_manager import AccountStateManager

logger = logging.getLogger(__name__)


class BacktestRunner:
    """Run strategies against historical price data stored in price_candles_5m."""

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        initial_usdt: float = 10000.0,
        strategy_names: list[str] | None = None,
        strategy_params: dict[str, dict] | None = None,
    ):
        self.symbol = symbol
        self.initial_usdt = initial_usdt
        self.strategy_names = strategy_names or ["lot_stacking"]
        self.strategy_params = strategy_params or {}
        self.results: dict = {}

    async def run(self, start_ts_ms: int, end_ts_ms: int) -> dict:
        """Run backtest over historical candle data.

        Returns a result dict with PnL summary, trade count and final balances.
        """
        # ------------------------------------------------------------------
        # Load candles
        # ------------------------------------------------------------------
        async with TradingSessionLocal() as session:
            stmt = (
                select(PriceCandle5m)
                .where(
                    PriceCandle5m.symbol == self.symbol,
                    PriceCandle5m.ts_ms >= start_ts_ms,
                    PriceCandle5m.ts_ms <= end_ts_ms,
                )
                .order_by(PriceCandle5m.ts_ms)
            )
            result = await session.execute(stmt)
            candles = result.scalars().all()

        if not candles:
            return {"error": "No candle data found for the given range"}

        logger.info(
            "Backtest: %s candles loaded for %s [%d – %d]",
            len(candles),
            self.symbol,
            start_ts_ms,
            end_ts_ms,
        )

        # ------------------------------------------------------------------
        # Initialise backtest exchange client
        # ------------------------------------------------------------------
        client = BacktestClient(
            symbol=self.symbol,
            initial_balance_usdt=self.initial_usdt,
        )

        # Ephemeral account_id – state lives only in this DB session scope
        account_id = uuid4()

        # Create strategy instances once (they hold no mutable state themselves)
        strategies: dict[str, object] = {}
        for name in self.strategy_names:
            try:
                strategies[name] = StrategyRegistry.create_instance(name)
            except KeyError:
                logger.warning("Backtest: unknown strategy '%s', skipping", name)

        if not strategies:
            return {"error": "No valid strategies found"}

        # ------------------------------------------------------------------
        # Replay loop
        # ------------------------------------------------------------------
        for candle in candles:
            price = float(candle.close)
            client.set_price(price)

            async with TradingSessionLocal() as session:
                for name, strategy in strategies.items():
                    state = StrategyStateStore(account_id, name, session)
                    shared_state = AccountStateManager(account_id, session)
                    params = self.strategy_params.get(name, {})

                    # Build a minimal RepositoryBundle lazily imported to
                    # avoid circular imports at module level.
                    from app.db.lot_repo import LotRepository
                    from app.db.order_repo import OrderRepository
                    from app.db.position_repo import PositionRepository
                    from app.strategies.base import RepositoryBundle, StrategyContext

                    repos = RepositoryBundle(
                        lot=LotRepository(session),
                        order=OrderRepository(session),
                        position=PositionRepository(session),
                        price=None,  # price_repo uses module-level functions
                    )

                    base_asset = self.symbol.replace("USDT", "")
                    ctx = StrategyContext(
                        account_id=account_id,
                        symbol=self.symbol,
                        base_asset=base_asset,
                        quote_asset="USDT",
                        current_price=price,
                        params={**strategy.default_params, **params},
                        client_order_prefix=f"bt_{name[:4]}_",
                    )

                    try:
                        await strategy.tick(ctx, state, client, shared_state, repos)
                    except Exception as exc:
                        logger.warning(
                            "Backtest tick error (strategy=%s, price=%.2f): %s",
                            name,
                            price,
                            exc,
                        )

                await session.commit()

        # ------------------------------------------------------------------
        # Summarise results
        # ------------------------------------------------------------------
        final_price = float(candles[-1].close)
        btc_bal = client._balances.get("BTC", {"free": 0.0, "locked": 0.0})
        usdt_bal = client._balances.get("USDT", {"free": 0.0, "locked": 0.0})

        btc_total = btc_bal["free"] + btc_bal["locked"]
        usdt_total = usdt_bal["free"] + usdt_bal["locked"]
        btc_value = btc_total * final_price
        final_value = btc_value + usdt_total

        self.results = {
            "symbol": self.symbol,
            "period": {
                "start_ts_ms": start_ts_ms,
                "end_ts_ms": end_ts_ms,
                "candle_count": len(candles),
            },
            "initial_value_usdt": self.initial_usdt,
            "final_value_usdt": round(final_value, 2),
            "pnl_usdt": round(final_value - self.initial_usdt, 2),
            "pnl_pct": round(
                (final_value - self.initial_usdt) / self.initial_usdt * 100, 2
            ),
            "total_trades": len(client._trades),
            "strategies": self.strategy_names,
            "final_balances": {
                "USDT": round(usdt_total, 2),
                "BTC_qty": round(btc_total, 8),
                "BTC_value_usdt": round(btc_value, 2),
            },
        }
        return self.results
