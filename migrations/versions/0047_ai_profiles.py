"""Профили игроков для Тёмного друна (ai_profiles).

Богатое досье на каждого игрока: саммари личности, манера речи, структурные
данные (черты/темы/связи), снимок статы. Обновляется в реальном времени и
свипом. Без FK (соглашение проекта). Идемпотентно.

Revision ID: 0047_ai_profiles
Revises: 0046_drun_econ_index
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0047_ai_profiles"
down_revision: Union[str, None] = "0046_drun_econ_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS ai_profiles (
            user_id BIGINT NOT NULL PRIMARY KEY,
            summary TEXT,
            speech_style TEXT,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            stats JSONB NOT NULL DEFAULT '{}'::jsonb,
            messages_seen INTEGER NOT NULL DEFAULT 0,
            refreshed_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_ai_profiles_refreshed ON ai_profiles (refreshed_at)",
    ]
    for stmt in statements:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_profiles")
