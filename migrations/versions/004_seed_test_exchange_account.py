"""Seed smoke-test exchange account for execution service.

Revision ID: 004
Revises: 003
Create Date: 2026-06-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USER_ID = "00000000-0000-0000-0000-000000000001"
_ACCOUNT_ID = "00000000-0000-0000-0000-000000000003"


def upgrade() -> None:
    # Execution service default_account_id must be a valid exchange_accounts FK.
    # Set DEFAULT_ACCOUNT_ID=00000000-0000-0000-0000-000000000003 on execution service.
    op.execute(sa.text(f"""
        INSERT INTO exchange_accounts (
            id, user_id, venue, account_label,
            trading_mode, approval_level, is_active,
            created_at, updated_at
        ) VALUES (
            '{_ACCOUNT_ID}'::uuid, '{_USER_ID}'::uuid,
            'binance', 'smoke-test',
            'paper', 'l2_paper', true,
            now(), now()
        )
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    op.execute(sa.text(
        f"DELETE FROM exchange_accounts WHERE id = '{_ACCOUNT_ID}'::uuid"
    ))
