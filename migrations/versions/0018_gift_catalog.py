"""Gift catalog: ассортимент Telegram Gifts для магазина (этап 1, без выдачи).

Создаёт ``gift_catalog`` — каталог подарков, которые игроки СМОГУТ покупать за
ешки (магазин Gifts из VOZNYA_ECONOMY_V2). На этом этапе — ТОЛЬКО каталог и
админ-управление: цена в ешках, себестоимость в Stars (для P&L), запас/резерв.
Автоматической отправки через Telegram API и потока покупки здесь НЕТ — это
следующий этап (поле ``stock`` и ``reserved`` уже заложены под него).

Почему отдельная таблица, а не ``shop_offers``: shop_offers продаёт предметы
каталога ``inventory_items`` (косметику). Gift — это РЕАЛЬНЫЙ Telegram-подарок,
не предмет инвентаря: у него есть себестоимость в Stars (твой расход) и привязка
к telegram gift id, чего у косметики нет. Разделение полей делает экономику Gifts
(бюджет в Stars, наценка в ешках) явной и не смешивает с косметикой.

Экономика (см. VOZNYA_ECONOMY_V2 §3–4): 1 Star ≈ 10 ешек; цена в ешках обычно
= star_cost*10*наценка. ``stock`` — пул доступных к продаже единиц (бюджет в
штуках, пополняется вручную); ``reserved`` — задел под будущий резерв при покупке
(чтобы не продать больше пула при гонках). На этапе 1 оба поля просто хранятся.

Связи логические (без FK): ``telegram_gift_id`` — id подарка у Telegram (когда
будет известен). НЕ затрагивает другие таблицы.

Revision ID: 0018_gift_catalog
Revises: 0017_seed_vagabond_case
Create Date: 2026-06-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_gift_catalog"
down_revision: Union[str, None] = "0017_seed_vagabond_case"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gift_catalog",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # Стабильный машинный код, например 'gift_heart', 'gift_rose'.
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # Себестоимость подарка в Telegram Stars (твой расход при отправке).
        sa.Column("star_cost", sa.Integer(), nullable=False),
        # Цена для игрока в ешках (списывается через экономику при покупке).
        sa.Column("price_eshki", sa.BigInteger(), nullable=False),
        # Id подарка у Telegram (если уже известен; для будущей авто-выдачи).
        sa.Column("telegram_gift_id", sa.Text(), nullable=True),
        # --- Запас/бюджет в штуках (задел под выдачу; этап 1 — только хранит) ---
        # Сколько единиц доступно к продаже (пул, пополняется вручную).
        # NULL = безлимит (не рекомендуется для реального расхода Stars).
        sa.Column("stock", sa.Integer(), nullable=True),
        # Зарезервировано под незавершённые покупки (этап 2). Пока 0.
        sa.Column(
            "reserved", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        # Сколько уже продано (для отчётности/лимитов).
        sa.Column(
            "sold_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        # Витрина: доступен ли к показу/продаже.
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        # Порядок сортировки на витрине (меньше — выше).
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("100")
        ),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.UniqueConstraint("code", name="uq_gift_catalog_code"),
        sa.CheckConstraint("star_cost >= 0", name="ck_gift_catalog_star_nonneg"),
        sa.CheckConstraint("price_eshki >= 0", name="ck_gift_catalog_price_nonneg"),
        sa.CheckConstraint("reserved >= 0", name="ck_gift_catalog_reserved_nonneg"),
        sa.CheckConstraint("sold_count >= 0", name="ck_gift_catalog_sold_nonneg"),
        # Остаток не уходит в минус: резерв не превышает запас (когда задан).
        sa.CheckConstraint(
            "stock IS NULL OR reserved <= stock",
            name="ck_gift_catalog_reserved_le_stock",
        ),
    )
    # Снимаем server_default'ы — приложение задаёт значения явно (как в проекте).
    op.alter_column("gift_catalog", "reserved", server_default=None)
    op.alter_column("gift_catalog", "sold_count", server_default=None)
    op.alter_column("gift_catalog", "is_active", server_default=None)
    op.alter_column("gift_catalog", "sort_order", server_default=None)
    op.create_index(
        "ix_gift_catalog_active", "gift_catalog", ["is_active", "sort_order"]
    )


def downgrade() -> None:
    op.drop_index("ix_gift_catalog_active", table_name="gift_catalog")
    op.drop_table("gift_catalog")
