"""stars_ledger: единый журнал движения Telegram Stars бота (приход/расход).

Источник правды на актив «Stars» (параллель ``transactions`` для ешек). Нужен,
потому что Telegram хранит только текущий баланс (`getMyStarBalance`), но не нашу
бизнес-историю: кто пополнил, за что списали, какой charge_id. Без него нельзя
корректно считать P&L и восстанавливать операции. См. STARS_FUNDING_GUIDE,
app/models/stars_ledger.py.

Новая таблица, ничего не затрагивает. ``charge_id`` UNIQUE → дедуп входящих
платежей (один платёж не зачислится дважды).

Revision ID: 0022_stars_ledger
Revises: 0021_gift_tx_tg_gift_kind
Create Date: 2026-06-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_stars_ledger"
down_revision: Union[str, None] = "0021_gift_tx_tg_gift_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stars_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("amount_stars", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("charge_id", sa.String(length=128), nullable=True),
        sa.Column("ref", sa.String(length=128), nullable=True),
        sa.Column(
            "source", sa.String(length=16), nullable=False,
            server_default=sa.text("'bot'"),
        ),
        sa.Column("balance_after", sa.BigInteger(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("charge_id", name="uq_stars_ledger_charge"),
        sa.CheckConstraint("direction IN ('in','out')", name="ck_stars_direction"),
        sa.CheckConstraint("amount_stars > 0", name="ck_stars_amount_positive"),
    )
    # Снимаем server_default — приложение задаёт source явно (конвенция проекта).
    op.alter_column("stars_ledger", "source", server_default=None)
    op.create_index("ix_stars_ledger_created", "stars_ledger", ["created_at"])
    op.create_index(
        "ix_stars_ledger_user", "stars_ledger", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_stars_ledger_reason", "stars_ledger", ["reason", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_stars_ledger_reason", table_name="stars_ledger")
    op.drop_index("ix_stars_ledger_user", table_name="stars_ledger")
    op.drop_index("ix_stars_ledger_created", table_name="stars_ledger")
    op.drop_table("stars_ledger")
