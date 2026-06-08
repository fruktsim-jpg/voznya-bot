"""Схема: app_settings — редактируемые из админки настройки (Admin V2, Этап 9).

Ключ-значение в JSONB: позволяет менять цены/веса/шансы/кулдауны без миграций и
деплоя. Бот читает их через ``app.settings.dynamic`` (кэш с TTL), сайт-админка
правит через ``/api/admin/settings``. Если ключа нет — код использует дефолт из
``app/settings/balance.py`` (БД лишь ПЕРЕОПРЕДЕЛЯЕТ, не заменяет источник истины
кода). Поэтому таблица может быть пустой — поведение остаётся прежним.

Поля:
* ``key``        — стабильный идентификатор настройки (например ``casino.max_bet``);
* ``value``      — JSONB значение (число/строка/объект/массив);
* ``category``   — группировка для UI (economy/casino/farm/gifts/...);
* ``description``— человекочитаемое описание для админки;
* ``updated_by`` — admin user_id последнего редактора (аудит);
* ``updated_at`` — когда.

Revision ID: 0032_app_settings
Revises: 0031_seed_cases_v2
Create Date: 2026-06-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0032_app_settings"
down_revision: Union[str, None] = "0031_seed_cases_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="general"),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_app_settings_category", "app_settings", ["category"]
    )


def downgrade() -> None:
    op.drop_index("ix_app_settings_category", table_name="app_settings")
    op.drop_table("app_settings")
