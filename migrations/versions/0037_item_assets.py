"""Схема: item_assets — авторский слой арта предметов (Item Authoring IA-1).

Pattern A: сайт ВЛАДЕЕТ контент-каталогом и пишет в эти таблицы через
аудируемые admin-API (`app/api/admin/assets`). Бот остаётся авторитетом
экономики/балансов/грантов/выдачи — он сюда не пишет, но таблица живёт в общей
БД, поэтому DDL заводится здесь (единый runner миграций — Alembic бота).

Назначение: вынести арт предметов из статического кода
(`v0-voznya/lib/item-art/manifest.ts`, ~10 захардкоженных SVG) в данные, чтобы
новый предмет/арт появлялся БЕЗ коммита разработчика. Байты картинки хранятся в
БД (`bytes`), отдаются кэширующим роутом `/api/items/asset/{code}`; resolver
(`resolveItemArt`) читает опубликованный манифест, а не хардкод-карту.

Поля:
* ``code``        — стабильный item-code (== inventory_items.code / любой code арта);
* ``mime``        — image/png | image/webp (валидируется на загрузке);
* ``bytes``       — оптимизированные байты картинки (bytea);
* ``width/height``— размеры пикселей (для превью/верстки);
* ``byte_size``   — размер в байтах (лимиты/аудит);
* ``checksum``    — sha256 для дедупликации/идемпотентности;
* ``placeholder`` — крошечный data-URL/blurhash для плавной загрузки (nullable);
* ``status``      — draft | published | retired (lifecycle публикации);
* ``version``     — растёт при каждой замене байтов (busting кэша по ?v=);
* ``uploaded_by`` — admin user_id (аудит); ``created_at/updated_at``.

Пустая таблица безопасна: resolver продолжает падать в статический seed-манифест
и глифы, ничего не ломается до первой публикации.

Revision ID: 0037_item_assets
Revises: 0036_pending_msg_id
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_item_assets"
down_revision: Union[str, None] = "0036_pending_msg_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "item_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("mime", sa.String(length=32), nullable=False),
        sa.Column("bytes", sa.LargeBinary(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("placeholder", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="draft"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("uploaded_by", sa.BigInteger(), nullable=True),
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
    op.create_index("ix_item_assets_status", "item_assets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_item_assets_status", table_name="item_assets")
    op.drop_table("item_assets")
