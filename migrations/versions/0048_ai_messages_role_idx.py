"""Индекс ai_messages (role, created_at) для профилей/свипа друна.

Новые горячие запросы (свип активных, аудитория «писали за N минут», граф
со-упоминаний) фильтруют ``role='chat' AND created_at >= ...``. Существующие
индексы ``(channel, created_at)`` и ``(user_id, created_at)`` этому паттерну не
помогают (``role`` — отдельная колонка). Добавляем покрывающий индекс.
Идемпотентно.

Revision ID: 0048_ai_messages_role_idx
Revises: 0047_ai_profiles
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0048_ai_messages_role_idx"
down_revision: Union[str, None] = "0047_ai_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_messages_role_created "
        "ON ai_messages (role, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ai_messages_role_created")
