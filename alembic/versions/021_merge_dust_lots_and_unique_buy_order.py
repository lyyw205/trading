"""Merge dust lots and add unique constraint on buy_order_id

Step 1: Update CheckConstraint to allow MERGED status
Step 2: Merge duplicate OPEN lots (same account_id + buy_order_id, sell_order_id IS NULL)
Step 3: Create partial unique index to prevent future duplicates

Revision ID: 021
Revises: 020
Create Date: 2026-03-13
"""

import sqlalchemy as sa
from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Update CheckConstraint to allow MERGED status
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE lots DROP CONSTRAINT IF EXISTS chk_lot_status"))
    op.create_check_constraint(
        "chk_lot_status",
        "lots",
        "status IN ('OPEN', 'CLOSED', 'MERGED')",
    )

    # Step 2: Merge duplicate dust lots
    # Only merge OPEN lots with sell_order_id IS NULL (protect active Binance orders)
    # For each group of duplicates: keep MIN(lot_id), sum buy_qty, weighted avg buy_price

    # Count before
    before = conn.execute(sa.text("SELECT COUNT(*) FROM lots WHERE status = 'OPEN'")).scalar()
    print(f"  [021] OPEN lots before merge: {before}")

    # Find duplicate groups and merge
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
    print(f"  [021] OPEN lots after merge: {after}, MERGED: {merged}")

    # Step 3: Create partial unique index
    op.create_index(
        "idx_lots_unique_buy_order",
        "lots",
        ["account_id", "buy_order_id"],
        unique=True,
        postgresql_where=sa.text("buy_order_id IS NOT NULL AND status = 'OPEN' AND sell_order_id IS NULL"),
    )


def downgrade() -> None:
    # Drop unique index
    op.drop_index("idx_lots_unique_buy_order", "lots")

    # Revert MERGED lots to OPEN (best effort — quantities not restored)
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE lots SET status = 'OPEN' WHERE status = 'MERGED'"))

    # Restore original CheckConstraint
    op.drop_constraint("chk_lot_status", "lots")
    op.create_check_constraint(
        "chk_lot_status",
        "lots",
        "status IN ('OPEN', 'CLOSED')",
    )
