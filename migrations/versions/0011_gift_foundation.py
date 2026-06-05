"""Gift foundation: передача активов между игроками.

Создаёт ``gift_transactions`` (журнал подарков предметов и ешек) и добавляет
колонку ``inventory_items.transferable`` (можно ли дарить предмет).

НЕ затрагивает: users, баланс, transactions(структуру), inventory/
inventory_history(структуру), admin_roles, audit_log, OIDC, account_links.
Передача предметов идёт через inventory (revoke+grant), денег — через
transactions (две проводки), всё в одной транзакции. Связи логические, без FK.

Защита от двойной отправки — уникальный ``idempotency_key``.

Revision ID: 0011_gift_foundation
Revises: 0010_shop_foundation
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_gift_foundation"
down_revision: Union[str, None] = "0010_shop_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Новая колонка каталога: можно ли дарить/передавать предмет.
    # server_default='true' — существующие предметы остаются передаваемыми;
    # затем дефолт снимаем, чтобы значение задавалось приложением явно.
    op.add_column(
        "inventory_items",
        sa.Column(
            "transferable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.alter_column("inventory_items", "transferable", server_default=None)

    # --- gift_transactions ---------------------------------------------------
    op.create_table(
        "gift_transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("gift_type", sa.String(length=16), nullable=False),
        sa.Column("sender_user_id", sa.BigInteger(), nullable=True),
        sa.Column("recipient_user_id", sa.BigInteger(), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
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
        sa.UniqueConstraint(
            "idempotency_key", name="uq_gift_idempotency_key"
        ),
        sa.CheckConstraint(
            "sender_user_id IS NULL OR sender_user_id <> recipient_user_id",
            name="ck_gift_not_self",
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount > 0", name="ck_gift_amount_positive"
        ),
        sa.CheckConstraint(
            "(kind = 'item' AND item_code IS NOT NULL) OR "
            "(kind = 'currency' AND amount IS NOT NULL)",
            name="ck_gift_kind_payload",
        ),
    )
    op.create_index(
        "ix_gift_recipient",
        "gift_transactions",
        ["recipient_user_id", "created_at"],
    )
    op.create_index(
        "ix_gift_sender",
        "gift_transactions",
        ["sender_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_gift_sender", table_name="gift_transactions")
    op.drop_index("ix_gift_recipient", table_name="gift_transactions")
    op.drop_table("gift_transactions")

    op.drop_column("inventory_items", "transferable")
