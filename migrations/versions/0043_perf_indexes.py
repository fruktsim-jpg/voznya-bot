"""Индексы под горячие выборки: недельный топ и очередь вывода подарков.

Добавляет два индекса под уже существующие запросы (без изменения схемы):

* ``ix_transactions_created_at`` — btree по ``transactions.created_at``. Нужен
  недельному топу заработка (``weekly_top_earners``: фильтр
  ``amount > 0 AND created_at >= since`` + агрегат), чтобы не сканировать весь
  журнал транзакций за период.
* ``ix_gift_tx_pending_withdraw`` — частичный btree по ``(status, created_at)``
  только для строк ``kind = 'tg_gift'``. Покрывает воркер авто-вывода и
  административную очередь (``get_withdraw_requested`` / ``get_pending_deliveries``:
  ``kind='tg_gift' AND status='pending' ... ORDER BY created_at``). Частичность по
  редкому ``kind='tg_gift'`` держит индекс маленьким; ``meta @> {...}``
  (после снятия лишнего cast) доостаётся фильтром на уже суженном наборе.

Revision ID: 0043_perf_indexes
Revises: 0042_case_gift_rewards
Create Date: 2026-06-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043_perf_indexes"
down_revision: Union[str, None] = "0042_case_gift_rewards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_transactions_created_at",
        "transactions",
        ["created_at"],
    )
    op.create_index(
        "ix_gift_tx_pending_withdraw",
        "gift_transactions",
        ["status", "created_at"],
        postgresql_where=sa.text("kind = 'tg_gift'"),
    )


def downgrade() -> None:
    op.drop_index("ix_gift_tx_pending_withdraw", table_name="gift_transactions")
    op.drop_index("ix_transactions_created_at", table_name="transactions")
