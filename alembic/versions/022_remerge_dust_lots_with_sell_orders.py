"""Re-merge dust lots that had active sell orders

After user cancelled all open sell orders on Binance,
clear sell_order_id and re-run merge for remaining duplicates.

Revision ID: 022
Revises: 021
Create Date: 2026-03-13
"""

import sqlalchemy as sa
from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1: Clear sell_order_id for all OPEN lots (user cancelled all on Binance)
    conn.execute(
        sa.text("""
            UPDATE lots
            SET sell_order_id = NULL, sell_order_time_ms = NULL
            WHERE status = 'OPEN' AND sell_order_id IS NOT NULL
        """)
    )

    # Step 2: Re-merge remaining duplicates
    before = conn.execute(sa.text("SELECT COUNT(*) FROM lots WHERE status = 'OPEN'")).scalar()
    print(f"  [022] OPEN lots before merge: {before}")

    # Update keeper with summed qty and weighted avg price
    conn.execute(
        sa.text("""
            WITH dup_groups AS (
                SELECT account_id, buy_order_id,
                       MIN(lot_id) AS keeper_id,
                       SUM(buy_qty) AS total_qty,
                       SUM(buy_price * buy_qty) / NULLIF(SUM(buy_qty), 0) AS weighted_price
                FROM lots
                WHERE status = 'OPEN'
                  AND buy_order_id IS NOT NULL
                  AND sell_order_id IS NULL
                GROUP BY account_id, buy_order_id
                HAVING COUNT(*) > 1
            )
            UPDATE lots
            SET buy_qty = dup_groups.total_qty,
                buy_price = dup_groups.weighted_price
            FROM dup_groups
            WHERE lots.lot_id = dup_groups.keeper_id
              AND lots.account_id = dup_groups.account_id
        """)
    )

    # Mark non-keeper duplicates as MERGED
    conn.execute(
        sa.text("""
            WITH dup_groups AS (
                SELECT account_id, buy_order_id, MIN(lot_id) AS keeper_id
                FROM lots
                WHERE status = 'OPEN'
                  AND buy_order_id IS NOT NULL
                  AND sell_order_id IS NULL
                GROUP BY account_id, buy_order_id
                HAVING COUNT(*) > 1
            )
            UPDATE lots
            SET status = 'MERGED'
            FROM dup_groups
            WHERE lots.account_id = dup_groups.account_id
              AND lots.buy_order_id = dup_groups.buy_order_id
              AND lots.lot_id != dup_groups.keeper_id
              AND lots.status = 'OPEN'
              AND lots.sell_order_id IS NULL
        """)
    )

    after = conn.execute(sa.text("SELECT COUNT(*) FROM lots WHERE status = 'OPEN'")).scalar()
    merged = conn.execute(sa.text("SELECT COUNT(*) FROM lots WHERE status = 'MERGED'")).scalar()
    print(f"  [022] OPEN lots after merge: {after}, MERGED: {merged}")


def downgrade() -> None:
    # Best effort: revert MERGED lots from this migration to OPEN
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE lots SET status = 'OPEN' WHERE status = 'MERGED'"))
