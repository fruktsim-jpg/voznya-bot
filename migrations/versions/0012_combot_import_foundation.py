"""Combot historical import foundation: хранилище исторической статистики.

Создаёт 4 изолированные таблицы под одноразовый импорт истории из Combot:
  * ``combot_import_runs``     — журнал прогонов импорта (идемпотентность/аудит);
  * ``combot_user_stats``      — пер-юзерный снимок (messages/xp/rep/joined);
  * ``combot_daily_stats``     — дневные агрегаты (messages/active/joins/leaves);
  * ``combot_activity_heatmap``— тепловая карта 24×7 (hour×weekday→messages).

НЕ затрагивает: users, баланс, transactions, inventory/inventory_history,
shop_*, gift_transactions, admin_roles, audit_log, OIDC, account_links.
Связи с combot_import_runs — логические (import_run_id), без FK, чтобы данные
истории были самодостаточны и переживали любые чистки.

Применять ВРУЧНУЮ (`alembic upgrade head`) и НЕ в production до решения.
Автоматический прогон не предусмотрен.

Revision ID: 0012_combot_import_foundation
Revises: 0011_gift_foundation
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_combot_import_foundation"
down_revision: Union[str, None] = "0011_gift_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- combot_import_runs --------------------------------------------------
    op.create_table(
        "combot_import_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'running'"),
        ),
        sa.Column("range_from_ms", sa.BigInteger(), nullable=True),
        sa.Column("range_to_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "users_imported",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "days_imported",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "heatmap_cells_imported",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("started_by", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column(
            "meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="ck_combot_run_status",
        ),
    )

    # --- combot_user_stats ---------------------------------------------------
    op.create_table(
        "combot_user_stats",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("days_since_joined", sa.Integer(), nullable=True),
        sa.Column(
            "messages",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "xp", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "rep", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "last_message_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("import_run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_combot_user_messages", "combot_user_stats", ["messages"]
    )
    op.create_index(
        "ix_combot_user_joined", "combot_user_stats", ["joined_at"]
    )

    # --- combot_daily_stats --------------------------------------------------
    op.create_table(
        "combot_daily_stats",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column(
            "messages",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "active_users",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "joins", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "leaves",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("import_run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("day"),
    )

    # --- combot_activity_heatmap ---------------------------------------------
    op.create_table(
        "combot_activity_heatmap",
        sa.Column("hour", sa.SmallInteger(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column(
            "messages",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("import_run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("hour", "weekday"),
        sa.CheckConstraint(
            "hour >= 0 AND hour <= 23", name="ck_combot_heatmap_hour"
        ),
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_combot_heatmap_weekday",
        ),
    )


def downgrade() -> None:
    op.drop_table("combot_activity_heatmap")
    op.drop_table("combot_daily_stats")
    op.drop_index("ix_combot_user_joined", table_name="combot_user_stats")
    op.drop_index("ix_combot_user_messages", table_name="combot_user_stats")
    op.drop_table("combot_user_stats")
    op.drop_table("combot_import_runs")
