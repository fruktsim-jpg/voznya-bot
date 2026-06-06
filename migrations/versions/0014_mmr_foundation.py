"""Система рейтинга MMR: журнал изменений единого игрового рейтинга.

Создаёт ОДНУ изолированную таблицу ``mmr_entries`` — источник правды общего
игрового прогресса. Одна строка = одно изменение рейтинга (начисление/списание)
с источником, причиной и временем. Текущий MMR и топы — агрегаты по журналу
(``SUM(amount)``), поэтому значение всегда пересчитывается из истории.

НЕ затрагивает: users, баланс/transactions, репутацию (reputation_entries),
messages_count, cooldowns, inventory/inventory_history, shop_*,
gift_transactions, combot_*, OIDC, account_links, admin_roles, audit_log.

MMR начисляется только за игровые действия (клад, дуэль, ферма, ачивки,
ивенты, админ-награды). Сообщения рейтинг НЕ дают.

Revision ID: 0014_mmr_foundation
Revises: 0013_reputation_foundation
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_mmr_foundation"
down_revision: Union[str, None] = "0013_reputation_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mmr_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Текущий MMR и топы: агрегаты по игроку.
    op.create_index("ix_mmr_player", "mmr_entries", ["player_id"])
    # Хронология/история игрока.
    op.create_index(
        "ix_mmr_player_created",
        "mmr_entries",
        ["player_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_mmr_player_created", table_name="mmr_entries")
    op.drop_index("ix_mmr_player", table_name="mmr_entries")
    op.drop_table("mmr_entries")
