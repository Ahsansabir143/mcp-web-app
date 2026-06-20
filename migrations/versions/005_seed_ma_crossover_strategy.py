"""Seed BTCUSDT EMA9/EMA21 moving-average crossover strategy (paper only).

Revision ID: 005
Revises: 004
Create Date: 2026-06-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USER_ID = "00000000-0000-0000-0000-000000000001"
_STRATEGY_ID = "00000000-0000-0000-0000-000000000010"
_VERSION_ID = "00000000-0000-0000-0000-000000000011"

# Parameters stored as JSON — factory dispatches on strategy_type
_PARAMETERS = """{
    "strategy_type": "ma_crossover",
    "interval": "1m",
    "size_usd": 100.0,
    "order_type": "MARKET",
    "stop_loss_pct": 1.5,
    "take_profit_pct": 3.0,
    "time_in_force": "GTC"
}"""


def upgrade() -> None:
    op.execute(sa.text(f"""
        INSERT INTO strategies (
            id, user_id, name, description,
            market_type, symbol_filters, state, current_version,
            created_at, updated_at
        ) VALUES (
            '{_STRATEGY_ID}'::uuid, '{_USER_ID}'::uuid,
            'BTCUSDT MA Crossover (Paper)',
            'EMA9/EMA21 crossover strategy on BTCUSDT spot. '
            'BUY when EMA9 crosses above EMA21, SELL when below. '
            'Paper mode only — no real orders placed.',
            'spot', '["BTCUSDT"]'::jsonb, 'paper_active', 1,
            now(), now()
        )
        ON CONFLICT DO NOTHING
    """))

    op.execute(sa.text(f"""
        INSERT INTO strategy_versions (
            id, strategy_id, version,
            rules, parameters, approval_required,
            change_note, created_by, created_at_ms
        ) VALUES (
            '{_VERSION_ID}'::uuid, '{_STRATEGY_ID}'::uuid, 1,
            '[]'::jsonb,
            '{_PARAMETERS}'::jsonb,
            'l1_simulation',
            'Initial MA crossover seed — EMA9/EMA21, 1m interval, $100 size_usd',
            'migration',
            0
        )
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    op.execute(sa.text(
        f"DELETE FROM strategy_versions WHERE id = '{_VERSION_ID}'::uuid"
    ))
    op.execute(sa.text(
        f"DELETE FROM strategies WHERE id = '{_STRATEGY_ID}'::uuid"
    ))
