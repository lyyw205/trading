"""Add indexes for fills/orders and CHECK constraints for buy_pause_state/role

Revision ID: 024
Revises: 023
Create Date: 2026-03-17
"""

from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Indexes ---
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_fills_account_order "
        "ON fills (account_id, order_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_symbol "
        "ON orders (symbol)"
    )

    # --- CHECK constraints (NOT VALID then VALIDATE for zero-downtime) ---
    op.execute(
        "ALTER TABLE trading_accounts ADD CONSTRAINT chk_buy_pause_state "
        "CHECK (buy_pause_state IN ('ACTIVE', 'THROTTLED', 'PAUSED')) NOT VALID"
    )
    op.execute("ALTER TABLE trading_accounts VALIDATE CONSTRAINT chk_buy_pause_state")

    op.execute(
        "ALTER TABLE user_profiles ADD CONSTRAINT chk_user_role "
        "CHECK (role IN ('user', 'admin')) NOT VALID"
    )
    op.execute("ALTER TABLE user_profiles VALIDATE CONSTRAINT chk_user_role")


def downgrade() -> None:
    op.execute("ALTER TABLE user_profiles DROP CONSTRAINT IF EXISTS chk_user_role")
    op.execute("ALTER TABLE trading_accounts DROP CONSTRAINT IF EXISTS chk_buy_pause_state")
    op.execute("DROP INDEX IF EXISTS idx_orders_symbol")
    op.execute("DROP INDEX IF EXISTS idx_fills_account_order")
