"""Схема: inventory_items — lifecycle + availability + asset link (IA-2 Item Builder).

Owner directive: НЕ active/inactive, а полноценный lifecycle
(draft|review|scheduled|published|retired|archived) + окно доступности +
привязка к авторскому арту + updated_at для аудита. Это даёт Item Builder писать
реальные предметы из админки без кода.

Совместимость: существующая колонка `is_active` (её читает бот) СОХРАНЯЕТСЯ.
Добавляем `status` и бэкфиллим из is_active (active→published, inactive→draft),
чтобы текущие предметы остались видимыми. Сайт пишет `status` (источник истины
lifecycle на витринах сайта) и синхронно держит `is_active` для совместимости с
ботом до отдельной миграции чтения бота.

Новые поля:
* ``status``          — lifecycle-статус (см. lib/admin/lifecycle.ts);
* ``available_from``  — когда предмет становится доступен (для scheduled);
* ``available_until`` — когда снимается (кампании/лимитки), nullable;
* ``asset_code``      — код арта в item_assets (обычно == code; даёт share art);
* ``featured_slot``   — необязательная подсказка-поверхность (denormalized hint);
* ``updated_by``      — последний редактор; ``updated_at`` — когда.

Revision ID: 0040_item_lifecycle
Revises: 0039_featured_slots
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040_item_lifecycle"
down_revision: Union[str, None] = "0039_featured_slots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inventory_items",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
    )
    op.add_column(
        "inventory_items",
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inventory_items",
        sa.Column("available_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inventory_items",
        sa.Column("asset_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "inventory_items",
        sa.Column("featured_slot", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "inventory_items",
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "inventory_items",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Backfill: keep existing items live. active → published, inactive → draft.
    op.execute(
        "UPDATE inventory_items SET status = CASE WHEN is_active THEN 'published' ELSE 'draft' END"
    )
    # Default asset_code to the item code (shared art model: art keyed by code).
    op.execute("UPDATE inventory_items SET asset_code = code WHERE asset_code IS NULL")

    op.create_index("ix_inventory_items_status", "inventory_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_inventory_items_status", table_name="inventory_items")
    op.drop_column("inventory_items", "updated_at")
    op.drop_column("inventory_items", "updated_by")
    op.drop_column("inventory_items", "featured_slot")
    op.drop_column("inventory_items", "asset_code")
    op.drop_column("inventory_items", "available_until")
    op.drop_column("inventory_items", "available_from")
    op.drop_column("inventory_items", "status")
