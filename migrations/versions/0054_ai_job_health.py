"""Drun job health tracking.

Revision ID: 0054_ai_job_health
Revises: 0053_ai_chat_archive
Create Date: 2026-06-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0054_ai_job_health"
down_revision: Union[str, None] = "0053_ai_chat_archive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_job_health (
            job_name VARCHAR(96) PRIMARY KEY,
            last_run_at TIMESTAMP WITH TIME ZONE,
            last_success_at TIMESTAMP WITH TIME ZONE,
            last_error_at TIMESTAMP WITH TIME ZONE,
            last_duration_ms INTEGER,
            last_rows INTEGER,
            last_error TEXT,
            runs INTEGER NOT NULL DEFAULT 0,
            successes INTEGER NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0,
            meta JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_job_health_updated "
        "ON ai_job_health (updated_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_job_health")
