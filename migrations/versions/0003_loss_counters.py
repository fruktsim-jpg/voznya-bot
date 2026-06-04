"""Счётчики проигрышей для секретных достижений (ребаланс экономики).

Аддитивная и безопасная для существующих данных миграция:
новые столбцы создаются с server_default = 0.

Revision ID: 0003_loss_counters
Revises: 0002_achievements
Create Date: 2026-06-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_loss_counters"
down_revision: Union[str, None] = "0002_achievements"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("casino_loss_streak", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("duel_loss_streak", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("max_casino_loss", sa.BigInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "max_casino_loss")
    op.drop_column("users", "duel_loss_streak")
    op.drop_column("users", "casino_loss_streak")
