"""Inventory foundation: каталог предметов, владение и история движений.

Создаёт три таблицы центрального инвентарного слоя:

* ``inventory_items`` — каталог (определения предметов: титулы, рамки, бейджи,
  аватары, коллекционные, событийные, будущие игровые);
* ``inventory`` — владение игроком + экипировка (1 активный предмет на слот);
* ``inventory_history`` — append-only леджер движений предметов.

НЕ затрагивает: users, transactions, баланс, экономику, account_links,
oidc_link_requests. Использует admin_roles/audit_log опосредованно (через
actor_user_id / audit_id), без изменения тех таблиц. Магазин здесь не создаётся.

Revision ID: 0009_inventory_foundation
Revises: 0008_admin_platform
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_inventory_foundation"
down_revision: Union[str, None] = "0008_admin_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- inventory_items (каталог) ------------------------------------------
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("slot", sa.String(length=16), nullable=True),
        sa.Column("rarity", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_limited", sa.Boolean(), nullable=False),
        sa.Column("max_supply", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_inventory_items_code"),
    )
    op.create_index(
        "ix_inventory_items_type_active",
        "inventory_items",
        ["type", "is_active"],
    )

    # --- inventory (владение + экипировка) ----------------------------------
    op.create_table(
        "inventory",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("slot", sa.String(length=16), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("equipped", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "item_code", name="uq_inventory_user_item"),
        sa.CheckConstraint("quantity >= 0", name="ck_inventory_quantity_nonneg"),
    )
    op.create_index("ix_inventory_user", "inventory", ["user_id"])
    # Максимум один экипированный предмет на слот у игрока (частичный уник-индекс).
    op.create_index(
        "uq_inventory_one_equipped_per_slot",
        "inventory",
        ["user_id", "slot"],
        unique=True,
        postgresql_where=sa.text("equipped = true AND slot IS NOT NULL"),
    )

    # --- inventory_history (леджер движений) --------------------------------
    op.create_table(
        "inventory_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("audit_id", sa.BigInteger(), nullable=True),
        sa.Column("transaction_id", sa.BigInteger(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_history_user",
        "inventory_history",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_inventory_history_item",
        "inventory_history",
        ["item_code", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_history_item", table_name="inventory_history")
    op.drop_index("ix_inventory_history_user", table_name="inventory_history")
    op.drop_table("inventory_history")

    op.drop_index("uq_inventory_one_equipped_per_slot", table_name="inventory")
    op.drop_index("ix_inventory_user", table_name="inventory")
    op.drop_table("inventory")

    op.drop_index(
        "ix_inventory_items_type_active", table_name="inventory_items"
    )
    op.drop_table("inventory_items")
