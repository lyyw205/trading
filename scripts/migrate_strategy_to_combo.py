"""
Migrate strategy_configs rows to trading_combos.

Usage:
    python scripts/migrate_strategy_to_combo.py --dry-run   # preview only
    python scripts/migrate_strategy_to_combo.py             # actually migrate
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from uuid import UUID

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from app.db.session import TradingSessionLocal
from app.models.strategy_config import StrategyConfig
from app.models.trading_combo import TradingCombo

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Sell-related parameter keys that should be extracted into sell_params
SELL_PARAM_KEYS = {"tp_pct", "base_price_update_mode"}


def _split_params(strategy_name: str, params: dict) -> tuple[dict, dict]:
    """Split legacy monolithic params into (buy_params, sell_params)."""
    buy_params = {}
    sell_params = {}
    for key, val in (params or {}).items():
        if key in SELL_PARAM_KEYS:
            sell_params[key] = val
        else:
            buy_params[key] = val
    return buy_params, sell_params


def _map_strategy(strategy_name: str) -> tuple[str, str]:
    """Map legacy strategy name -> (buy_logic_name, sell_logic_name)."""
    mapping = {
        "lot_stacking": ("lot_stacking", "fixed_tp"),
        "trend_buy": ("trend_buy", "fixed_tp"),
    }
    if strategy_name not in mapping:
        raise ValueError(f"Unknown legacy strategy: {strategy_name}")
    return mapping[strategy_name]


async def migrate(dry_run: bool = True):
    async with TradingSessionLocal() as session:
        # Load all strategy_configs
        sc_stmt = select(StrategyConfig).order_by(
            StrategyConfig.account_id, StrategyConfig.strategy_name
        )
        sc_result = await session.execute(sc_stmt)
        strategy_configs = list(sc_result.scalars().all())

        if not strategy_configs:
            logger.info("No strategy_configs found. Nothing to migrate.")
            return

        logger.info("Found %d strategy_config(s) to process.", len(strategy_configs))

        # Load existing combos to skip accounts that already have them
        combo_stmt = select(TradingCombo.account_id).distinct()
        combo_result = await session.execute(combo_stmt)
        accounts_with_combos = {row[0] for row in combo_result}

        # Group configs by account
        by_account: dict[UUID, list[StrategyConfig]] = {}
        for sc in strategy_configs:
            by_account.setdefault(sc.account_id, []).append(sc)

        created_count = 0
        skipped_count = 0

        for account_id, configs in by_account.items():
            if account_id in accounts_with_combos:
                logger.info(
                    "  SKIP account %s — already has combos", account_id
                )
                skipped_count += len(configs)
                continue

            # First pass: create lot_stacking combos (needed for reference_combo_id)
            lot_stacking_combo_id: UUID | None = None

            for sc in configs:
                if sc.strategy_name != "lot_stacking":
                    continue
                buy_logic, sell_logic = _map_strategy(sc.strategy_name)
                buy_params, sell_params = _split_params(sc.strategy_name, sc.params)

                combo = TradingCombo(
                    account_id=account_id,
                    name=f"{sc.strategy_name} (migrated)",
                    buy_logic_name=buy_logic,
                    buy_params=buy_params,
                    sell_logic_name=sell_logic,
                    sell_params=sell_params,
                    is_enabled=sc.is_enabled,
                )
                logger.info(
                    "  %s account=%s strategy=%s -> combo(buy=%s, sell=%s) enabled=%s",
                    "DRY-RUN" if dry_run else "CREATE",
                    account_id,
                    sc.strategy_name,
                    buy_logic,
                    sell_logic,
                    sc.is_enabled,
                )
                logger.info("    buy_params=%s", buy_params)
                logger.info("    sell_params=%s", sell_params)

                if not dry_run:
                    session.add(combo)
                    await session.flush()
                    lot_stacking_combo_id = combo.id
                created_count += 1

            # Second pass: create trend_buy combos (with reference_combo_id)
            for sc in configs:
                if sc.strategy_name != "trend_buy":
                    continue
                buy_logic, sell_logic = _map_strategy(sc.strategy_name)
                buy_params, sell_params = _split_params(sc.strategy_name, sc.params)

                combo = TradingCombo(
                    account_id=account_id,
                    name=f"{sc.strategy_name} (migrated)",
                    buy_logic_name=buy_logic,
                    buy_params=buy_params,
                    sell_logic_name=sell_logic,
                    sell_params=sell_params,
                    reference_combo_id=lot_stacking_combo_id,
                    is_enabled=sc.is_enabled,
                )
                logger.info(
                    "  %s account=%s strategy=%s -> combo(buy=%s, sell=%s, ref=%s) enabled=%s",
                    "DRY-RUN" if dry_run else "CREATE",
                    account_id,
                    sc.strategy_name,
                    buy_logic,
                    sell_logic,
                    lot_stacking_combo_id,
                    sc.is_enabled,
                )
                logger.info("    buy_params=%s", buy_params)
                logger.info("    sell_params=%s", sell_params)

                if not dry_run:
                    session.add(combo)
                created_count += 1

            # Handle any other unknown strategies
            for sc in configs:
                if sc.strategy_name in ("lot_stacking", "trend_buy"):
                    continue
                logger.warning(
                    "  SKIP unknown strategy: account=%s strategy=%s",
                    account_id,
                    sc.strategy_name,
                )
                skipped_count += 1

        if not dry_run:
            await session.commit()

        logger.info(
            "Migration %s: created=%d, skipped=%d",
            "preview" if dry_run else "complete",
            created_count,
            skipped_count,
        )


def main():
    parser = argparse.ArgumentParser(description="Migrate strategy_configs to trading_combos")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()
    asyncio.run(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
