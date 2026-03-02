"""Fix timezone-naive datetime columns

Revision ID: 012
Revises: 011
Create Date: 2026-03-02
"""
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # buy_pause_since was created as TIMESTAMP (without tz) in migration 007.
    # All other datetime columns use TIMESTAMP WITH TIME ZONE.
    op.execute(
        "ALTER TABLE trading_accounts "
        "ALTER COLUMN buy_pause_since TYPE TIMESTAMP WITH TIME ZONE"
    )

    # Candle tables created in 008 used DateTime() without timezone for created_at.
    for table in ("price_candles_1m", "price_candles_1h", "price_candles_1d"):
        op.execute(
            f"ALTER TABLE {table} "
            "ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE"
        )


def downgrade() -> None:
    for table in ("price_candles_1d", "price_candles_1h", "price_candles_1m"):
        op.execute(
            f"ALTER TABLE {table} "
            "ALTER COLUMN created_at TYPE TIMESTAMP WITHOUT TIME ZONE"
        )
    op.execute(
        "ALTER TABLE trading_accounts "
        "ALTER COLUMN buy_pause_since TYPE TIMESTAMP WITHOUT TIME ZONE"
    )
