"""Seed smoke-test user and strategy.

Revision ID: 003
Revises: 002
Create Date: 2026-06-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USER_ID = "00000000-0000-0000-0000-000000000001"
_STRATEGY_ID = "00000000-0000-0000-0000-000000000001"
_VERSION_ID = "00000000-0000-0000-0000-000000000002"


def upgrade() -> None:
    op.execute(sa.text("""
        INSERT INTO users (
            id, email, username, hashed_password,
            is_active, is_admin, plan, created_at, updated_at
        ) VALUES (
            :uid, 'smoke@test.local', 'smoke_test',
            '$2b$12$placeholder_not_for_login', true, false, 'free',
            now(), now()
        )
        ON CONFLICT DO NOTHING
    """).bindparams(uid=_USER_ID))

    op.execute(sa.text("""
        INSERT INTO strategies (
            id, user_id, name, description,
            market_type, symbol_filters, state, current_version,
            created_at, updated_at
        ) VALUES (
            :sid, :uid, 'Smoke Test Strategy',
            'Auto-seeded strategy for smoke tests.',
            'spot', '[]'::jsonb, 'paper_active', 1,
            now(), now()
        )
        ON CONFLICT DO NOTHING
    """).bindparams(sid=_STRATEGY_ID, uid=_USER_ID))

    op.execute(sa.text("""
        INSERT INTO strategy_versions (
            id, strategy_id, version,
            rules, parameters, approval_required,
            change_note, created_by, created_at_ms
        ) VALUES (
            :vid, :sid, 1,
            '[]'::jsonb, '{}'::jsonb, 'l1_simulation',
            'Initial seed version', 'migration', 0
        )
        ON CONFLICT DO NOTHING
    """).bindparams(vid=_VERSION_ID, sid=_STRATEGY_ID))


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM strategy_versions WHERE id = :vid").bindparams(vid=_VERSION_ID)
    )
    op.execute(
        sa.text("DELETE FROM strategies WHERE id = :sid").bindparams(sid=_STRATEGY_ID)
    )
    op.execute(
        sa.text("DELETE FROM users WHERE id = :uid").bindparams(uid=_USER_ID)
    )
