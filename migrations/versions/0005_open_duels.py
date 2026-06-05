"""Поддержка открытых вызовов на дуэль.

Изменяет pending_actions.target_id на nullable, чтобы можно было создавать
открытые вызовы (любой может принять), когда target_id=NULL.

Revision ID: 0005_open_duels
Revises: 0004_message_stats
Create Date: 2026-06-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_open_duels"
down_revision: Union[str, None] = "0004_message_stats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Изменяем target_id на nullable для поддержки открытых вызовов
    op.alter_column(
        "pending_actions",
        "target_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    # Откат: делаем target_id обратно NOT NULL
    # ВНИМАНИЕ: это может упасть, если в БД есть записи с target_id=NULL
    op.alter_column(
        "pending_actions",
        "target_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
