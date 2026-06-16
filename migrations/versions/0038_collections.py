"""Схема: collections — авторский реестр коллекций (Collections Foundation).

Pattern A: сайт владеет контент-каталогом; коллекции — первоклассная сущность
платформы, а НЕ свойство, прикрученное к предмету задним числом. Предметы
рождаются «collection-aware»: `inventory_items.collection_code` уже существует
(см. cities_nl / founders), эта таблица даёт ему авторскую родительскую запись
(имя, описание, тип, сезон, порядок, lifecycle-статус).

Статусы — общий lifecycle платформы (lib/admin/lifecycle.ts):
draft|review|scheduled|published|retired|archived. Публичные витрины показывают
только «живые» коллекции.

Поля:
* ``code``        — стабильный код коллекции (== inventory_items.collection_code);
* ``name``        — отображаемое имя;
* ``description`` — описание для витрины коллекции;
* ``kind``        — permanent|seasonal|event (как живёт коллекция);
* ``season_code`` — привязка к сезону (для seasonal/event), nullable;
* ``sort_order``  — порядок в списках;
* ``status``      — lifecycle-статус;
* ``created_by/updated_by`` — аудит; ``created_at/updated_at``.

Revision ID: 0038_collections
Revises: 0037_item_assets
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038_collections"
down_revision: Union[str, None] = "0037_item_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="permanent"),
        sa.Column("season_code", sa.String(length=64), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
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
    op.create_index("ix_collections_status", "collections", ["status"])

    # Backfill parent records for collection codes already in use, so existing
    # collectibles immediately have an authored collection (published, since
    # they're already live in the game).
    #
    # NB: `inventory_items.collection_code` is added later (0040). On a fresh
    # chain this migration runs before that column exists, so guard the backfill
    # with a column-existence check — otherwise it raises UndefinedColumnError
    # and the whole upgrade aborts. When the column is absent there's nothing to
    # backfill yet; 0040 carries no backfill, but any pre-existing codes are a
    # no-op here and collections can be authored normally afterwards.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'inventory_items'
               AND column_name = 'collection_code'
          ) THEN
            INSERT INTO collections (code, name, kind, status, sort_order)
            SELECT DISTINCT collection_code,
                   initcap(replace(collection_code, '_', ' ')),
                   'permanent', 'published', 100
              FROM inventory_items
             WHERE collection_code IS NOT NULL
            ON CONFLICT (code) DO NOTHING;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_collections_status", table_name="collections")
    op.drop_table("collections")
