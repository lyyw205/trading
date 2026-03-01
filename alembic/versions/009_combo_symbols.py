"""Add symbols to trading_combos and migrate strategy_state scope."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add symbols column
    op.add_column("trading_combos", sa.Column("symbols", JSONB, nullable=False, server_default="[]"))

    # 2. Data migration: populate symbols from account's symbol
    op.execute("""
        UPDATE trading_combos tc
        SET symbols = jsonb_build_array(ta.symbol)
        FROM trading_accounts ta
        WHERE ta.id = tc.account_id
          AND tc.symbols = '[]'::jsonb
    """)

    # 3. Add CHECK constraint for non-empty symbols
    op.create_check_constraint(
        "chk_symbols_not_empty",
        "trading_combos",
        "jsonb_array_length(symbols) > 0",
    )

    # 4. Migrate strategy_state scope: combo_id -> combo_id:symbol
    op.execute("""
        UPDATE strategy_state ss
        SET scope = ss.scope || ':' || ta.symbol
        FROM trading_combos tc
        JOIN trading_accounts ta ON ta.id = tc.account_id
        WHERE ss.scope = tc.id::text
          AND ss.scope NOT LIKE '%:%'
    """)


def downgrade() -> None:
    # Revert strategy_state scope
    op.execute("""
        UPDATE strategy_state
        SET scope = split_part(scope, ':', 1)
        WHERE scope LIKE '%:%'
    """)

    op.drop_constraint("chk_symbols_not_empty", "trading_combos", type_="check")
    op.drop_column("trading_combos", "symbols")
