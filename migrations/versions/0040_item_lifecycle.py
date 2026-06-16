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
    # Idempotent by design. Some production DBs were restored from a snapshot
    # whose `inventory_items` is missing columns that earlier migrations (0016)
    # were supposed to add (collection_code / series_total / stackable), while
    # alembic_version had drifted ahead. Replaying with plain ADD COLUMN then
    # aborts on the gaps. Using ADD COLUMN IF NOT EXISTS lets this migration
    # converge the table to the expected shape regardless of which columns are
    # already present — safe on a clean chain (all IF NOT EXISTS are no-ops).
    #
    # NB: asyncpg forbids multiple statements in one prepared query, so each
    # ALTER must be its own op.execute (no semicolon-batching).
    statements = [
        # Safety net for columns nominally added by 0016 but absent on some DBs.
        "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS collection_code varchar(64)",
        "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS series_total integer",
        "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS stackable boolean NOT NULL DEFAULT false",
        # 0040 lifecycle + availability + asset link.
        "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS status varchar(16) NOT NULL DEFAULT 'draft'",
        "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS available_from timestamptz",
        "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS available_until timestamptz",
        "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS asset_code varchar(64)",
        "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS featured_slot varchar(32)",
        "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS updated_by bigint",
        "ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()",
    ]
    for stmt in statements:
        op.execute(stmt)

    # Backfill: keep existing items live. active → published, inactive → draft.
    op.execute(
        "UPDATE inventory_items SET status = CASE WHEN is_active THEN 'published' ELSE 'draft' END"
    )
    # Default asset_code to the item code (shared art model: art keyed by code).
    op.execute("UPDATE inventory_items SET asset_code = code WHERE asset_code IS NULL")

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inventory_items_status ON inventory_items (status)"
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_items_status", table_name="inventory_items")
    op.drop_column("inventory_items", "updated_at")
    op.drop_column("inventory_items", "updated_by")
    op.drop_column("inventory_items", "featured_slot")
    op.drop_column("inventory_items", "asset_code")
    op.drop_column("inventory_items", "available_until")
    op.drop_column("inventory_items", "available_from")
    op.drop_column("inventory_items", "status")
