"""Shop foundation: shop_categories + shop_offers + purchase_history.

Магазин поверх существующих систем:

* ``shop_categories`` — разделы витрины;
* ``shop_offers`` — товары (предмет каталога + цена + лимит/сезон);
* ``purchase_history`` — история совершённых покупок.

НЕ затрагивает: users, баланс, transactions(структуру), inventory* (структуру),
admin_roles, audit_log, OIDC, account_links. Связи — логические (item_code →
inventory_items.code, transaction_id → transactions.id, audit_id → audit_log.id),
без FK (конвенция проекта).

Защита от двойной покупки лимитки на игрока — частичный уникальный индекс
``uq_purchase_user_offer_unique`` (см. purchase_history).

Revision ID: 0010_shop_foundation
Revises: 0009_inventory_foundation
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_shop_foundation"
down_revision: Union[str, None] = "0009_inventory_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- shop_categories -----------------------------------------------------
    op.create_table(
        "shop_categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_shop_categories_slug"),
    )

    # --- shop_offers ---------------------------------------------------------
    op.create_table(
        "shop_offers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("category_slug", sa.String(length=32), nullable=True),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("is_limited", sa.Boolean(), nullable=False),
        sa.Column("max_supply", sa.Integer(), nullable=True),
        sa.Column("sold_count", sa.Integer(), nullable=False),
        sa.Column("per_user_limit", sa.Integer(), nullable=True),
        sa.Column("is_seasonal", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("price >= 0", name="ck_shop_offers_price_nonneg"),
        sa.CheckConstraint("sold_count >= 0", name="ck_shop_offers_sold_nonneg"),
        sa.CheckConstraint(
            "max_supply IS NULL OR sold_count <= max_supply",
            name="ck_shop_offers_sold_le_supply",
        ),
    )
    op.create_index(
        "ix_shop_offers_category_active",
        "shop_offers",
        ["category_slug", "is_active"],
    )
    op.create_index("ix_shop_offers_item", "shop_offers", ["item_code"])

    # --- purchase_history ----------------------------------------------------
    op.create_table(
        "purchase_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("transaction_id", sa.BigInteger(), nullable=True),
        sa.Column("audit_id", sa.BigInteger(), nullable=True),
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
        "ix_purchase_history_user",
        "purchase_history",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_purchase_history_offer",
        "purchase_history",
        ["offer_id", "created_at"],
    )
    # Защита от двойной покупки «1 на руки»: уникальность (user_id, offer_id)
    # только для строк с meta.unique = 'true'.
    op.create_index(
        "uq_purchase_user_offer_unique",
        "purchase_history",
        ["user_id", "offer_id"],
        unique=True,
        postgresql_where=sa.text("(meta ->> 'unique') = 'true'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_purchase_user_offer_unique", table_name="purchase_history"
    )
    op.drop_index("ix_purchase_history_offer", table_name="purchase_history")
    op.drop_index("ix_purchase_history_user", table_name="purchase_history")
    op.drop_table("purchase_history")

    op.drop_index("ix_shop_offers_item", table_name="shop_offers")
    op.drop_index("ix_shop_offers_category_active", table_name="shop_offers")
    op.drop_table("shop_offers")

    op.drop_table("shop_categories")
