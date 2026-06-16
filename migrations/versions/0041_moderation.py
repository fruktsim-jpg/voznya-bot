"""Схема модерации: user_moderation + mod_warnings.

Combot-style модерация, общая для бота и сайта. Бот — источник истины
(он реально банит/мьютит через Telegram), сайт делает аудируемые записи.

Таблицы (без внешних ключей, по соглашению проекта):

* ``user_moderation`` — текущее состояние ограничений игрока: до какого
  времени забанен/замьючен и сколько активных варнов. Одна строка на игрока
  (``user_id`` это PK). NULL в ``banned_until``/``muted_until`` = нет ограничения.
* ``mod_warnings`` — append-only история варнов: кто, кому, когда, почему,
  активен ли ещё (снятые/протухшие → active=false). Порог активных варнов
  триггерит авто-мьют.

Действия дублируются в ``audit_log`` (player.ban/unban/mute/unmute/warn/unwarn)
для ленты «кто что сделал» в админ-панели.

Revision ID: 0041_moderation
Revises: 0040_item_lifecycle
Create Date: 2026-06-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041_moderation"
down_revision: Union[str, None] = "0040_item_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_moderation",
        sa.Column("user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        # NULL = не забанен / не замьючен. Хранится в UTC.
        sa.Column("banned_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
        # Денормализованный счётчик активных варнов (для быстрого порога).
        sa.Column("warn_count", sa.Integer(), nullable=False, server_default="0"),
        # Снимки причин последнего действия (для /modinfo и панели).
        sa.Column("ban_reason", sa.Text(), nullable=True),
        sa.Column("mute_reason", sa.Text(), nullable=True),
        # Флаг «состояние изменено вне Telegram (с сайта) — боту нужно
        # применить/снять ограничение в Telegram при ближайшем тике».
        sa.Column(
            "tg_pending", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        # Кто последним менял состояние (user_id админа/модератора).
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Частые выборки: «кто сейчас под ограничением» (для авто-снятия по таймеру).
    op.create_index(
        "ix_user_moderation_banned_until", "user_moderation", ["banned_until"]
    )
    op.create_index(
        "ix_user_moderation_muted_until", "user_moderation", ["muted_until"]
    )
    # Быстрый поиск записей, ждущих применения в Telegram (с сайта).
    op.create_index(
        "ix_user_moderation_tg_pending",
        "user_moderation",
        ["tg_pending"],
        postgresql_where=sa.text("tg_pending"),
    )

    op.create_table(
        "mod_warnings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        # Кто выдал варн (user_id админа/модератора).
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        # active=false → снят вручную или протух (TTL). Считаем только активные.
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        # Когда варн протухнет (NULL = не протухает).
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Лента варнов игрока + быстрый подсчёт активных.
    op.create_index(
        "ix_mod_warnings_user_active",
        "mod_warnings",
        ["user_id", "active", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_mod_warnings_user_active", table_name="mod_warnings")
    op.drop_table("mod_warnings")
    op.drop_index("ix_user_moderation_tg_pending", table_name="user_moderation")
    op.drop_index("ix_user_moderation_muted_until", table_name="user_moderation")
    op.drop_index("ix_user_moderation_banned_until", table_name="user_moderation")
    op.drop_table("user_moderation")
