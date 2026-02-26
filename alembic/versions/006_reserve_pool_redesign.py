"""Reserve pool redesign - pending_earnings_usdt column

Revision ID: 006
Revises: 005
Create Date: 2026-02-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. trading_accounts에 pending_earnings_usdt 컬럼 추가
    op.add_column(
        "trading_accounts",
        sa.Column(
            "pending_earnings_usdt",
            sa.Numeric(),
            nullable=False,
            server_default="0",
        ),
    )

    # CHECK 제약조건: 음수 방지
    op.create_check_constraint(
        "ck_pending_earnings_non_negative",
        "trading_accounts",
        "pending_earnings_usdt >= 0",
    )

    # 2. 기존 core_bucket_usdt 데이터를 계정 레벨로 합산하여 이관
    #    - core_bucket_usdt는 각 combo scope에 저장됨 (scope = combo_id 문자열)
    #    - 같은 account_id의 모든 combo core_bucket_usdt를 SUM
    #    - NULLIF(value, '') 사용: clear_keys()가 빈 문자열로 설정하므로 방어
    #    - 음수 core_bucket은 0으로 처리 (GREATEST)
    op.execute("""
        UPDATE trading_accounts ta
        SET pending_earnings_usdt = COALESCE(agg.total, 0)
        FROM (
            SELECT
                ss.account_id,
                SUM(
                    GREATEST(
                        CAST(NULLIF(ss.value, '') AS NUMERIC),
                        0
                    )
                ) AS total
            FROM strategy_state ss
            WHERE ss.key = 'core_bucket_usdt'
              AND ss.value IS NOT NULL
              AND ss.value != ''
              AND ss.scope != 'shared'
            GROUP BY ss.account_id
        ) agg
        WHERE ta.id = agg.account_id
          AND agg.total > 0;
    """)

    # 3. 이관 완료 후 core_bucket_usdt 키 값을 0으로 리셋
    op.execute("""
        UPDATE strategy_state
        SET value = '0'
        WHERE key = 'core_bucket_usdt';
    """)


def downgrade() -> None:
    op.drop_constraint("ck_pending_earnings_non_negative", "trading_accounts")
    op.drop_column("trading_accounts", "pending_earnings_usdt")
