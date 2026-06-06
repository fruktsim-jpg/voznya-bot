"""Денормализация MMR: проекция текущего рейтинга в ``users.mmr``.

Добавляет колонку ``users.mmr`` (текущее значение рейтинга) и заполняет её
агрегатом по журналу ``mmr_entries`` (``SUM(amount)``). После этого журнал
остаётся источником правды/аудитом, а быстрые чтения (профиль, рейтинг, сайт,
MMR-команды) идут из ``users.mmr``. Запись изменений (repositories.mmr.add_entry)
обновляет журнал и проекцию синхронно.

Затрагивает ТОЛЬКО ``users`` (новое поле ``mmr`` + индекс). Ничего не удаляет,
журнал не трогает. Баланс/transactions/репутация/messages/inventory/shop —
не затрагиваются.

Revision ID: 0015_user_mmr_projection
Revises: 0014_mmr_foundation
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_user_mmr_projection"
down_revision: Union[str, None] = "0014_mmr_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Колонка с дефолтом 0 (server_default, чтобы существующие строки
    #    получили валидное значение сразу, без NULL).
    op.add_column(
        "users",
        sa.Column(
            "mmr",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # 2. Бэкфилл проекции из журнала одним UPDATE ... FROM (агрегат по игроку).
    op.execute(
        """
        UPDATE users AS u
        SET mmr = agg.total
        FROM (
            SELECT player_id, COALESCE(SUM(amount), 0) AS total
            FROM mmr_entries
            GROUP BY player_id
        ) AS agg
        WHERE agg.player_id = u.user_id
        """
    )
    # 3. Индекс под ORDER BY mmr DESC (топ по рейтингу).
    op.create_index("ix_users_mmr", "users", ["mmr"])
    # 4. server_default больше не нужен — приложение всегда пишет значение явно.
    op.alter_column("users", "mmr", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_users_mmr", table_name="users")
    op.drop_column("users", "mmr")
