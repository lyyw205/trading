"""Add local auth columns to user_profiles

Revision ID: 003
Revises: 002_backtest_runs
Create Date: 2026-02-26
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002_backtest_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add password and security columns
    op.add_column("user_profiles", sa.Column("password_hash", sa.String(), nullable=True))
    op.add_column("user_profiles", sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False))
    op.add_column("user_profiles", sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("user_profiles", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_profiles", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))

    # email must be unique (used as login identifier)
    op.create_unique_constraint("uq_user_profiles_email", "user_profiles", ["email"])

    # Drop Supabase RLS helper function
    op.execute("DROP FUNCTION IF EXISTS public.is_admin()")


def downgrade() -> None:
    # Recreate is_admin() function
    op.execute("""
        CREATE OR REPLACE FUNCTION public.is_admin()
        RETURNS BOOLEAN
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.user_profiles
                WHERE id = auth.uid() AND role = 'admin'
            );
        $$;
    """)

    op.drop_constraint("uq_user_profiles_email", "user_profiles", type_="unique")
    op.drop_column("user_profiles", "password_changed_at")
    op.drop_column("user_profiles", "locked_until")
    op.drop_column("user_profiles", "failed_login_count")
    op.drop_column("user_profiles", "is_active")
    op.drop_column("user_profiles", "password_hash")
