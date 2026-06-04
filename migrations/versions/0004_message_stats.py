"""Подсчёт сообщений для сайта.

Аддитивная и безопасная миграция:
* в users добавляется messages_count BIGINT NOT NULL DEFAULT 0;
* создаётся таблица message_daily(user_id, day, count) с индексом по day.

Telegram не отдаёт историю — счёт идёт с момента деплоя.

Revision ID: 0004_message_stats
Revises: 0003_loss_counters
Create Date: 2026-06-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_message_stats"
down_revision: Union[str, None] = "0003_loss_counters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("messages_count", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_table(
        "message_daily",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("count", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_index("ix_message_daily_day", "message_daily", ["day"])


def downgrade() -> None:
    op.drop_index("ix_message_daily_day", table_name="message_daily")
    op.drop_table("message_daily")
    op.drop_column("users", "messages_count")
