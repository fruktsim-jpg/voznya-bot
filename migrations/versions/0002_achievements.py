"""Достижения и счётчики для прогрессии (релиз v1.2).

Миграция аддитивная и безопасная для существующих данных:
новые столбцы создаются с server_default = 0, новая таблица — пустая.

Revision ID: 0002_achievements
Revises: 0001_initial
Create Date: 2026-06-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_achievements"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Счётчики для достижений (по умолчанию 0 для уже существующих игроков).
    op.add_column(
        "users",
        sa.Column("farm_success_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("casino_games_count", sa.Integer(), server_default="0", nullable=False),
    )

    # Таблица открытых достижений.
    op.create_table(
        "user_achievements",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("code", sa.String(length=64), primary_key=True),
        sa.Column(
            "unlocked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("user_achievements")
    op.drop_column("users", "casino_games_count")
    op.drop_column("users", "farm_success_count")
