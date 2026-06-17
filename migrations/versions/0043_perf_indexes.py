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

Индексы создаются ``CONCURRENTLY`` (без блокировки записи на больших таблицах) в
autocommit-блоке — DDL идёт вне общей транзакции миграции. ``IF NOT EXISTS`` /
``IF EXISTS`` делают шаг идемпотентным (безопасен при повторном/частичном прогоне).

Revision ID: 0043_perf_indexes
Revises: 0042_case_gift_rewards
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0043_perf_indexes"
down_revision: Union[str, None] = "0042_case_gift_rewards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY нельзя выполнять внутри транзакции — открываем autocommit-блок.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transactions_created_at "
            "ON transactions (created_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_gift_tx_pending_withdraw "
            "ON gift_transactions (status, created_at) WHERE kind = 'tg_gift'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_gift_tx_pending_withdraw")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transactions_created_at")
