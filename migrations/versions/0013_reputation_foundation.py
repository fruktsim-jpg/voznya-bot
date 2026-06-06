"""Система репутации: журнал изменений репутации сообщества.

Создаёт ОДНУ изолированную таблицу ``reputation_entries`` — источник правды
социального рейтинга. Одна строка = одно изменение (+1/-1) от игрока игроку,
с фразой-причиной и временем. Текущая репутация и топы — агрегаты по журналу
(``SUM(value)``), поэтому значение всегда пересчитывается из истории.

НЕ затрагивает: users, баланс/transactions, XP, messages_count, cooldowns,
inventory/inventory_history, shop_*, gift_transactions, combot_*, OIDC,
account_links, admin_roles, audit_log.

Антиспам (1 раз / 12 ч на пару giver→target) проверяется по этому же журналу
через индекс ``ix_reputation_pair`` — отдельной таблицы кулдаунов нет.

Revision ID: 0013_reputation_foundation
Revises: 0012_combot_import_foundation
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_reputation_foundation"
down_revision: Union[str, None] = "0012_combot_import_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reputation_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("giver_user_id", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.SmallInteger(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("value IN (-1, 1)", name="ck_reputation_value"),
        sa.CheckConstraint(
            "giver_user_id <> target_user_id", name="ck_reputation_not_self"
        ),
    )
    # Текущая репутация и топы: агрегаты по получателю.
    op.create_index(
        "ix_reputation_target", "reputation_entries", ["target_user_id"]
    )
    # Антиспам: последнее изменение конкретной пары giver→target.
    op.create_index(
        "ix_reputation_pair",
        "reputation_entries",
        ["giver_user_id", "target_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reputation_pair", table_name="reputation_entries")
    op.drop_index("ix_reputation_target", table_name="reputation_entries")
    op.drop_table("reputation_entries")
