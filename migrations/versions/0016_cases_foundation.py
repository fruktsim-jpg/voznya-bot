"""Cases foundation: кейсы, дроп-листы, леджер открытий + задел под инстансы.

Создаёт слой кейсов поверх существующего инвентаря (стековая модель остаётся
без изменений):

* ``case_definitions`` — определение кейса (поведение при открытии). Кейс — это
  предмет каталога ``inventory_items`` с ``type='case'``; здесь описывается его
  стоимость открытия и расписание;
* ``case_rewards`` — дроп-лист кейса: возможные награды и их целочисленные веса;
* ``case_openings`` — append-only леджер открытий (что выпало, бросок, слепок
  весов) для полной воспроизводимости по логам;
* ``inventory_instances`` — per-instance владение под БУДУЩИЕ Telegram Gifts и
  серийные предметы. В V1 таблица создаётся, но рантаймом НЕ используется
  (пустая). Это страхует контракт схемы, чтобы Gifts не требовали миграции
  владения позже.

Плюс аддитивные необязательные колонки каталога (``stackable``,
``collection_code``, ``series_total``) — задел под косметику/коллекции/серийники
без будущей миграции.

НЕ затрагивает структурно: users, transactions, баланс, inventory,
inventory_history, shop_*, gift_transactions, reputation, mmr, admin_roles,
audit_log, OIDC, account_links. Связи логические по ``code``/``id`` — без FK
(конвенция проекта). Выдача предметов идёт через inventory/inventory_history,
движение ешек — через transactions; кейсы их не дублируют.

Revision ID: 0016_cases_foundation
Revises: 0015_user_mmr_projection
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_cases_foundation"
down_revision: Union[str, None] = "0015_user_mmr_projection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Аддитивные колонки каталога (безопасно) -----------------------------
    # stackable: фунгибельный ли предмет (живёт в стековом inventory). Будущие
    # per-instance предметы (gifts/серийники) будут помечаться stackable=false.
    # server_default='true' — существующие предметы остаются стековыми; затем
    # дефолт снимаем, чтобы приложение задавало значение явно (как transferable).
    op.add_column(
        "inventory_items",
        sa.Column(
            "stackable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.alter_column("inventory_items", "stackable", server_default=None)
    # Задел под коллекции и серийники (в V1 не используются).
    op.add_column(
        "inventory_items",
        sa.Column("collection_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "inventory_items",
        sa.Column("series_total", sa.Integer(), nullable=True),
    )

    # --- inventory_instances (только схема, рантайма в V1 нет) ----------------
    op.create_table(
        "inventory_instances",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        # owned | pending | granted | failed | consumed
        sa.Column("instance_state", sa.String(length=16), nullable=False),
        sa.Column("serial_no", sa.Integer(), nullable=True),
        sa.Column("telegram_gift_id", sa.Text(), nullable=True),
        sa.Column(
            "is_upgraded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("collection_code", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("audit_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.alter_column("inventory_instances", "is_upgraded", server_default=None)
    op.create_index(
        "ix_inventory_instances_user", "inventory_instances", ["user_id"]
    )
    op.create_index(
        "ix_inventory_instances_item", "inventory_instances", ["item_code"]
    )
    # Один телеграм-подарок не может принадлежать двум строкам.
    op.create_index(
        "uq_inventory_instance_tg_gift",
        "inventory_instances",
        ["telegram_gift_id"],
        unique=True,
        postgresql_where=sa.text("telegram_gift_id IS NOT NULL"),
    )

    # --- case_definitions ----------------------------------------------------
    op.create_table(
        "case_definitions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # → inventory_items.code (предмет-кейс, type='case'); один-к-одному.
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # free | currency  (задел: stars — позже, без миграции)
        sa.Column("open_cost_kind", sa.String(length=16), nullable=False),
        sa.Column(
            "open_cost_amount",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Списывает ли открытие 1 предмет-кейс из инвентаря игрока.
        sa.Column(
            "consumes_key",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("season_code", sa.String(length=32), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("item_code", name="uq_case_definitions_item"),
        sa.CheckConstraint(
            "open_cost_amount >= 0", name="ck_case_def_cost_nonneg"
        ),
        sa.CheckConstraint(
            "open_cost_kind IN ('free', 'currency', 'stars')",
            name="ck_case_def_cost_kind",
        ),
    )
    op.alter_column("case_definitions", "open_cost_amount", server_default=None)
    op.alter_column("case_definitions", "consumes_key", server_default=None)
    op.alter_column("case_definitions", "is_active", server_default=None)
    op.create_index(
        "ix_case_definitions_active", "case_definitions", ["is_active"]
    )

    # --- case_rewards (дроп-лист + веса) -------------------------------------
    op.create_table(
        "case_rewards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # → case_definitions.item_code (кейс, которому принадлежит дроп).
        sa.Column("case_item_code", sa.String(length=64), nullable=False),
        # V1: item | currency. Схема допускает tg_gift|stars (задел); код V1 их
        # отклоняет валидатором — расширение без миграции.
        sa.Column("reward_kind", sa.String(length=16), nullable=False),
        # → inventory_items.code, если reward_kind='item'.
        sa.Column("reward_item_code", sa.String(length=64), nullable=True),
        # Ешки, если reward_kind='currency'.
        sa.Column("amount", sa.BigInteger(), nullable=True),
        # Целочисленный вес; вероятность = weight / SUM(weight).
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column(
            "min_qty", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "max_qty", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        # Лимит выпадений (джекпот); NULL = без лимита.
        sa.Column("max_global_supply", sa.Integer(), nullable=True),
        sa.Column(
            "granted_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_jackpot",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("weight > 0", name="ck_case_rewards_weight_pos"),
        sa.CheckConstraint(
            "min_qty >= 1 AND max_qty >= min_qty", name="ck_case_rewards_qty"
        ),
        sa.CheckConstraint(
            "granted_count >= 0", name="ck_case_rewards_granted_nonneg"
        ),
        sa.CheckConstraint(
            "max_global_supply IS NULL OR granted_count <= max_global_supply",
            name="ck_case_rewards_supply",
        ),
        # Полезная нагрузка по виду награды. tg_gift/stars разрешены СХЕМОЙ
        # (задел), но запрещены кодом V1.
        sa.CheckConstraint(
            "(reward_kind = 'item' AND reward_item_code IS NOT NULL) "
            "OR (reward_kind = 'currency' AND amount IS NOT NULL AND amount > 0) "
            "OR (reward_kind IN ('tg_gift', 'stars'))",
            name="ck_case_rewards_kind_payload",
        ),
    )
    op.alter_column("case_rewards", "min_qty", server_default=None)
    op.alter_column("case_rewards", "max_qty", server_default=None)
    op.alter_column("case_rewards", "granted_count", server_default=None)
    op.alter_column("case_rewards", "is_jackpot", server_default=None)
    op.create_index(
        "ix_case_rewards_case", "case_rewards", ["case_item_code"]
    )

    # --- case_openings (леджер открытий, append-only) ------------------------
    op.create_table(
        "case_openings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("case_item_code", sa.String(length=64), nullable=False),
        # → case_rewards.id (что выпало). NULL только для аварийных случаев.
        sa.Column("reward_id", sa.Integer(), nullable=True),
        sa.Column("reward_kind", sa.String(length=16), nullable=False),
        sa.Column("reward_item_code", sa.String(length=64), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=True),
        sa.Column(
            "qty", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        # Выпавшее число в [0, SUM(weight)).
        sa.Column("roll", sa.Integer(), nullable=False),
        # Слепок дроп-листа на момент открытия (для воспроизводимости честности).
        sa.Column(
            "weight_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        # Задел под provably-fair (seed). В V1 может быть NULL.
        sa.Column("server_seed", sa.String(length=64), nullable=True),
        # Связи с другими леджерами (без FK).
        sa.Column("transaction_id", sa.BigInteger(), nullable=True),
        sa.Column("audit_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("roll >= 0", name="ck_case_openings_roll_nonneg"),
        sa.CheckConstraint("qty >= 1", name="ck_case_openings_qty_pos"),
    )
    op.alter_column("case_openings", "qty", server_default=None)
    op.create_index(
        "ix_case_openings_user", "case_openings", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_case_openings_case", "case_openings", ["case_item_code", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_case_openings_case", table_name="case_openings")
    op.drop_index("ix_case_openings_user", table_name="case_openings")
    op.drop_table("case_openings")

    op.drop_index("ix_case_rewards_case", table_name="case_rewards")
    op.drop_table("case_rewards")

    op.drop_index("ix_case_definitions_active", table_name="case_definitions")
    op.drop_table("case_definitions")

    op.drop_index(
        "uq_inventory_instance_tg_gift", table_name="inventory_instances"
    )
    op.drop_index(
        "ix_inventory_instances_item", table_name="inventory_instances"
    )
    op.drop_index(
        "ix_inventory_instances_user", table_name="inventory_instances"
    )
    op.drop_table("inventory_instances")

    op.drop_column("inventory_items", "series_total")
    op.drop_column("inventory_items", "collection_code")
    op.drop_column("inventory_items", "stackable")
