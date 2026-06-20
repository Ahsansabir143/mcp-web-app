"""Add connection/stream status columns to exchange_accounts.

Revision ID: 006
Revises: 005
Create Date: 2026-06-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "exchange_accounts",
        sa.Column("connection_status", sa.String(32), nullable=False, server_default="disconnected"),
    )
    op.add_column(
        "exchange_accounts",
        sa.Column("last_connectivity_check_ms", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "exchange_accounts",
        sa.Column("listen_key", sa.String(256), nullable=True),
    )
    op.add_column(
        "exchange_accounts",
        sa.Column("listen_key_expires_ms", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "exchange_accounts",
        sa.Column("stream_status", sa.String(32), nullable=False, server_default="stopped"),
    )
    op.add_column(
        "exchange_accounts",
        sa.Column("stream_last_event_ms", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "exchange_accounts",
        sa.Column("stream_error", sa.String(512), nullable=True),
    )
    # Widen iv column to hold two base64 nonces separated by "|"
    op.alter_column(
        "api_credentials_ref",
        "iv",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=False,
    )


def downgrade() -> None:
    for col in (
        "stream_error",
        "stream_last_event_ms",
        "stream_status",
        "listen_key_expires_ms",
        "listen_key",
        "last_connectivity_check_ms",
        "connection_status",
    ):
        op.drop_column("exchange_accounts", col)
    op.alter_column(
        "api_credentials_ref",
        "iv",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=False,
    )
