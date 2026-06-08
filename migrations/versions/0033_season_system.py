"""Схема: сезонная система Возни (Сезон 1).

Создаёт таблицы сезона и сезонной прогрессии + колонку ``users.season_mmr``:
* ``seasons``                — сами сезоны (активен максимум один);
* ``season_mmr_entries``     — журнал сезонного MMR (копится с нуля);
* ``season_titles``          — выданные в финале сезонные титулы;
* ``login_streaks``          — серия ежедневных заходов;
* ``daily_claims``           — факт получения ежедневной награды (анти-двойной);
* ``weekly_mission_progress``— прогресс недельных заданий.

Связи логические (без FK) — конвенция проекта. Сезонные данные сбрасываются
вайпом (см. миграцию 0034 и docs/SEASON_1_WIPE_AND_DESIGN.md).

Revision ID: 0033_season_system
Revises: 0032_app_settings
Create Date: 2026-06-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_season_system"
down_revision: Union[str, None] = "0032_app_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seasons",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "season_mmr_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_season_mmr_season_player",
        "season_mmr_entries",
        ["season_id", "player_id"],
    )

    op.create_table(
        "season_titles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column(
            "awarded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "season_id", "player_id", "code", name="uq_season_title"
        ),
    )
    op.create_index("ix_season_titles_player", "season_titles", ["player_id"])

    op.create_table(
        "login_streaks",
        sa.Column("player_id", sa.BigInteger(), primary_key=True),
        sa.Column("last_login_date", sa.Date(), nullable=True),
        sa.Column("current_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("best_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "daily_claims",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("claim_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("streak_day", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("player_id", "claim_date", name="uq_daily_claim"),
    )

    op.create_table(
        "weekly_mission_progress",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("mission_code", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "player_id", "week_start", "mission_code", name="uq_weekly_mission"
        ),
    )
    op.create_index(
        "ix_weekly_mission_player_week",
        "weekly_mission_progress",
        ["player_id", "week_start"],
    )

    # Денормализованный сезонный MMR на пользователе (быстрые чтения).
    op.add_column(
        "users",
        sa.Column(
            "season_mmr", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_index("ix_users_season_mmr", "users", ["season_mmr"])


def downgrade() -> None:
    op.drop_index("ix_users_season_mmr", table_name="users")
    op.drop_column("users", "season_mmr")
    op.drop_index(
        "ix_weekly_mission_player_week", table_name="weekly_mission_progress"
    )
    op.drop_table("weekly_mission_progress")
    op.drop_table("daily_claims")
    op.drop_table("login_streaks")
    op.drop_index("ix_season_titles_player", table_name="season_titles")
    op.drop_table("season_titles")
    op.drop_index("ix_season_mmr_season_player", table_name="season_mmr_entries")
    op.drop_table("season_mmr_entries")
    op.drop_table("seasons")
