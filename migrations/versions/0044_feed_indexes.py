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
    op.create_index("ix_case_openings_created_at", "case_openings", ["created_at"])
    op.create_index(
        "ix_gift_tx_status_created", "gift_transactions", ["status", "created_at"]
    )
    op.create_index(
        "ix_user_achievements_unlocked_at", "user_achievements", ["unlocked_at"]
    )
    op.create_index("ix_marriages_married_at", "marriages", ["married_at"])
    op.create_index("ix_mmr_entries_created_at", "mmr_entries", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mmr_entries_created_at", table_name="mmr_entries")
    op.drop_index("ix_marriages_married_at", table_name="marriages")
    op.drop_index("ix_user_achievements_unlocked_at", table_name="user_achievements")
    op.drop_index("ix_gift_tx_status_created", table_name="gift_transactions")
    op.drop_index("ix_case_openings_created_at", table_name="case_openings")
