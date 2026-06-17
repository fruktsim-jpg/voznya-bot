"""Индексы под ленту событий сайта (community/user feed).

Лента (`v0-voznya/lib/feed.ts`) делает по каждому журналу
``... ORDER BY created_at DESC LIMIT n`` с временной границей
(``created_at >= NOW() - INTERVAL '90 days'``). Без индекса по времени это
полный скан + сортировка всей истории на каждый заход на главную/профиль.

Существующие индексы покрывают только доступ ПО игроку
(``ix_case_openings_user (user_id, created_at)`` и т.п.) — глобальная лента
по ним не ускоряется. Здесь добавляем индексы под глобальную выборку по
времени (всё аддитивно, без изменения схемы):

* ``ix_case_openings_created_at`` — ``case_openings(created_at)``.
* ``ix_gift_tx_status_created`` — ``gift_transactions(status, created_at)``
  (лента берёт ``status='completed' ORDER BY created_at``).
* ``ix_user_achievements_unlocked_at`` — ``user_achievements(unlocked_at)``.
* ``ix_marriages_married_at`` — ``marriages(married_at)``.
* ``ix_mmr_entries_created_at`` — ``mmr_entries(created_at)``.

``transactions(created_at)`` уже покрыт миграцией ``0043``.

Индексы создаются ``CONCURRENTLY`` (без блокировки записи на больших
append-only журналах) в autocommit-блоке — DDL идёт вне общей транзакции
миграции. ``IF NOT EXISTS`` / ``IF EXISTS`` делают шаг идемпотентным
(безопасен при повторном или частично применённом прогоне).

Revision ID: 0044_feed_indexes
Revises: 0043_perf_indexes
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0044_feed_indexes"
down_revision: Union[str, None] = "0043_perf_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY нельзя выполнять внутри транзакции — открываем autocommit-блок.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_case_openings_created_at "
            "ON case_openings (created_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_gift_tx_status_created "
            "ON gift_transactions (status, created_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_user_achievements_unlocked_at "
            "ON user_achievements (unlocked_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_marriages_married_at "
            "ON marriages (married_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mmr_entries_created_at "
            "ON mmr_entries (created_at)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_mmr_entries_created_at")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_marriages_married_at")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_user_achievements_unlocked_at")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_gift_tx_status_created")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_case_openings_created_at")
