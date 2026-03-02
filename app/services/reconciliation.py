"""Exchange <-> DB reconciliation service.

Periodic automatic execution (every 10 min) + admin API manual trigger.
On drift detection: log + alert + record event in DB.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.order_repo import OrderRepository
from app.db.position_repo import PositionRepository
from app.exchange.base_client import ExchangeClient
from app.models.fill import Fill
from app.models.position import Position
from app.services.alert_service import get_alert_service

logger = logging.getLogger(__name__)


@dataclass
class PositionDiff:
    symbol: str
    db_qty: float
    exchange_qty: float
    diff_qty: float
    diff_pct: float


@dataclass
class BalanceDiff:
    asset: str
    db_total: float
    exchange_total: float
    diff: float


@dataclass
class FillGap:
    symbol: str
    last_db_trade_id: int
    exchange_latest_trade_id: int
    estimated_missing: int


@dataclass
class ReconciliationResult:
    account_id: UUID
    timestamp: datetime
    position_diffs: list[PositionDiff] = field(default_factory=list)
    balance_diff: BalanceDiff | None = None
    fill_gaps: list[FillGap] = field(default_factory=list)
    status: str = "ok"  # "ok" | "drift_detected" | "error"

    def to_dict(self) -> dict:
        return {
            "account_id": str(self.account_id),
            "timestamp": self.timestamp.isoformat(),
            "position_diffs": [asdict(d) for d in self.position_diffs],
            "balance_diff": asdict(self.balance_diff) if self.balance_diff else None,
            "fill_gaps": [asdict(g) for g in self.fill_gaps],
            "status": self.status,
        }


class ReconciliationService:
    """Verify consistency between exchange state and DB records."""

    # Minimum % difference to report (0.01% = 0.0001)
    POSITION_THRESHOLD_PCT = 0.0001

    def __init__(self, session: AsyncSession, exchange: ExchangeClient):
        self._session = session
        self._exchange = exchange

    async def reconcile_account(self, account_id: UUID) -> ReconciliationResult:
        """Full reconciliation: positions + fill gaps."""
        try:
            position_diffs = await self._check_positions(account_id)
            fill_gaps = await self._check_fill_gaps(account_id)

            status = "ok"
            if position_diffs or fill_gaps:
                status = "drift_detected"

            result = ReconciliationResult(
                account_id=account_id,
                timestamp=datetime.now(UTC),
                position_diffs=position_diffs,
                fill_gaps=fill_gaps,
                status=status,
            )

            if status == "drift_detected":
                logger.warning("Reconciliation drift for %s: %d position diffs, %d fill gaps",
                               account_id, len(position_diffs), len(fill_gaps))
                await self._send_drift_alert(result)

            return result

        except Exception as e:
            logger.error("Reconciliation error for %s: %s", account_id, e)
            return ReconciliationResult(
                account_id=account_id,
                timestamp=datetime.now(UTC),
                status="error",
            )

    async def _check_positions(self, account_id: UUID) -> list[PositionDiff]:
        """Compare DB positions vs exchange account balances."""
        # 1. DB positions
        stmt = select(Position).where(Position.account_id == account_id)
        result = await self._session.execute(stmt)
        db_positions = list(result.scalars().all())

        if not db_positions:
            return []

        # 2. Exchange balances
        try:
            account_info = await self._exchange.get_account_info()
        except Exception as e:
            logger.warning("Failed to fetch exchange account info for %s: %s", account_id, e)
            return []

        exchange_balances: dict[str, float] = {}
        for b in account_info.get("balances", []):
            total = float(b.get("free", 0)) + float(b.get("locked", 0))
            if total > 0:
                exchange_balances[b["asset"]] = total

        # 3. Compare
        diffs = []
        for pos in db_positions:
            if pos.qty <= 0:
                continue
            # Extract base asset from symbol (e.g., ETHUSDT -> ETH)
            base_asset = pos.symbol.replace("USDT", "").replace("BUSD", "")
            ex_qty = exchange_balances.get(base_asset, 0.0)
            diff = pos.qty - ex_qty
            pct = abs(diff) / max(float(pos.qty), 1e-8)
            if pct > self.POSITION_THRESHOLD_PCT:
                diffs.append(PositionDiff(
                    symbol=pos.symbol,
                    db_qty=float(pos.qty),
                    exchange_qty=ex_qty,
                    diff_qty=round(diff, 8),
                    diff_pct=round(pct * 100, 4),
                ))
        return diffs

    async def _check_fill_gaps(self, account_id: UUID) -> list[FillGap]:
        """Check for missing fills by comparing last DB trade_id vs exchange."""
        # Get last trade_id per symbol from DB
        stmt = (
            select(Fill.symbol, func.max(Fill.trade_id))
            .where(Fill.account_id == account_id)
            .group_by(Fill.symbol)
        )
        result = await self._session.execute(stmt)
        db_last_ids = {row[0]: row[1] for row in result.all()}

        if not db_last_ids:
            return []

        gaps = []
        for symbol, last_db_id in db_last_ids.items():
            try:
                # Fetch latest single trade from exchange
                trades = await self._exchange.get_my_trades(symbol, limit=1)
                if not trades:
                    continue
                exchange_latest_id = int(trades[-1]["id"])
                if exchange_latest_id > last_db_id:
                    estimated_missing = exchange_latest_id - last_db_id
                    # Only report if gap is significant (>5 trades)
                    if estimated_missing > 5:
                        gaps.append(FillGap(
                            symbol=symbol,
                            last_db_trade_id=last_db_id,
                            exchange_latest_trade_id=exchange_latest_id,
                            estimated_missing=estimated_missing,
                        ))
            except Exception as e:
                logger.warning("Fill gap check failed for %s/%s: %s", account_id, symbol, e)

        return gaps

    async def repair_fill_gaps(self, account_id: UUID, symbol: str) -> int:
        """Repair missing fills using fromId-based fetching. Returns count of fills added."""
        # Get last DB trade_id for this symbol
        stmt = (
            select(func.max(Fill.trade_id))
            .where(Fill.account_id == account_id, Fill.symbol == symbol)
        )
        result = await self._session.execute(stmt)
        last_id = result.scalar()

        if last_id is None:
            return 0

        # Fetch from exchange starting after last known trade
        try:
            trades = await self._exchange.get_my_trades_from_id(symbol, from_id=last_id + 1)
        except Exception as e:
            logger.error("Failed to fetch trades from exchange for repair: %s", e)
            return 0

        if not trades:
            return 0

        # Insert missing fills
        order_repo = OrderRepository(self._session)
        fill_rows = [(int(t.get("orderId", 0)), t) for t in trades]
        await order_repo.insert_fills_batch(account_id, fill_rows)

        # Recompute position
        pos_repo = PositionRepository(self._session)
        await pos_repo.recompute_from_fills(account_id, symbol)

        logger.info("Repaired %d fills for %s/%s", len(trades), account_id, symbol)
        return len(trades)

    async def _send_drift_alert(self, result: ReconciliationResult) -> None:
        """Send alert on drift detection."""
        try:
            alert = get_alert_service()
            parts = [f"Reconciliation drift detected for account {result.account_id}"]
            if result.position_diffs:
                parts.append(f"Position diffs: {len(result.position_diffs)}")
                for d in result.position_diffs[:3]:  # limit to first 3
                    parts.append(f"  {d.symbol}: DB={d.db_qty:.6f} Ex={d.exchange_qty:.6f} ({d.diff_pct:.2f}%)")
            if result.fill_gaps:
                parts.append(f"Fill gaps: {len(result.fill_gaps)}")
                for g in result.fill_gaps[:3]:
                    parts.append(f"  {g.symbol}: ~{g.estimated_missing} missing")
            await alert.send_high("\n".join(parts))
        except Exception as e:
            logger.warning("Failed to send drift alert: %s", e)
