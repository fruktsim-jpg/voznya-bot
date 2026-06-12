"""Схема: featured_slots — авторская модель «избранного» (Featured Slots).

Owner directive: больше никаких эвристических «featured item». Один движок —
много потребителей. Surface-keyed слоты, которые редактор заполняет вручную;
поверхности (HOME/SHOP/CASES/PLAY/CASINO/SEASON _HERO) читают их через единый
резолвер (lib/featured.ts).

Pattern A: чистый контент-каталог (что показать в герое), НЕ экономика. Ссылка
полиморфная: (ref_type, ref_code) → item|case|collection|gift.

Статусы — общий lifecycle (draft|review|scheduled|published|retired|archived);
плюс окно доступности available_from/until для запланированных кампаний.

Поля:
* ``surface``     — ключ поверхности (HOME_HERO, SHOP_HERO, ...);
* ``ref_type``    — item|case|collection|gift;
* ``ref_code``    — код целевой сущности;
* ``title/subtitle`` — необязательный авторский текст оверлея;
* ``priority``    — для нескольких слотов на поверхности (меньше = выше);
* ``available_from/until`` — окно показа (для scheduled-кампаний);
* ``status``      — lifecycle;
* ``created_by/updated_by`` + ``created_at/updated_at``.

Revision ID: 0039_featured_slots
Revises: 0038_collections
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039_featured_slots"
down_revision: Union[str, None] = "0038_collections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "featured_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("surface", sa.String(length=32), nullable=False),
        sa.Column("ref_type", sa.String(length=16), nullable=False),
        sa.Column("ref_code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=True),
        sa.Column("subtitle", sa.String(length=256), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_until", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(
        "ix_featured_slots_surface_status",
        "featured_slots",
        ["surface", "status", "priority"],
    )


def downgrade() -> None:
    op.drop_index("ix_featured_slots_surface_status", table_name="featured_slots")
    op.drop_table("featured_slots")
