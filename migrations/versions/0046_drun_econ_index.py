"""Индекс под дневной лимит эконом-выходок друна (налог/подачка).

``app/features/drun/econ.py:_ops_today`` считает операции друна за сутки:
``... WHERE reason IN ('drun_tax','drun_grant') AND created_at >= NOW()-1d``.
Существующий ``ix_transactions_user_reason (user_id, reason)`` для этого
бесполезен — ведущая колонка ``user_id`` в запросе отсутствует, значит идёт
скан всего леджера. При включённой власть-фиче (``econ_enabled``) это
выполняется на каждое адресное обращение к друну.

Добавляем ``ix_transactions_reason_created (reason, created_at)`` — покрывает
выборку по причине с временной границей. Аддитивно, без изменения схемы.

Индекс создаётся ``CONCURRENTLY`` (без блокировки записи на большом
append-only леджере) в autocommit-блоке. ``IF NOT EXISTS`` / ``IF EXISTS``
делают шаги идемпотентными.

Revision ID: 0046_drun_econ_index
Revises: 0045_ai_drun
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0046_drun_econ_index"
down_revision: Union[str, None] = "0045_ai_drun"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY нельзя выполнять внутри транзакции — открываем autocommit-блок.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transactions_reason_created "
            "ON transactions (reason, created_at)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transactions_reason_created")
