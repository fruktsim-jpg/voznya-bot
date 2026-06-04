"""Начальная схема базы данных бота «Возня».

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users --------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=256), nullable=True),
        sa.Column("balance", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_earned", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_spent", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("farm_streak", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_farm_streak", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_farm_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("treasures_found", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duels_won", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duels_lost", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pidor_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_users_last_active_at", "users", ["last_active_at"])
    op.create_index("ix_users_balance", "users", ["balance"])

    # --- transactions -------------------------------------------------------
    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    op.create_index(
        "ix_transactions_user_reason", "transactions", ["user_id", "reason"]
    )

    # --- cooldowns ----------------------------------------------------------
    op.create_table(
        "cooldowns",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("action", sa.String(length=32), primary_key=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- daily_nominations --------------------------------------------------
    op.create_table(
        "daily_nominations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("nomination_type", sa.String(length=32), nullable=False),
        sa.Column("nomination_date", sa.Date(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id_2", sa.BigInteger(), nullable=True),
        sa.Column("opened_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "nomination_type", "nomination_date", name="uq_nomination_type_date"
        ),
    )

    # --- marriages ----------------------------------------------------------
    op.create_table(
        "marriages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id_1", sa.BigInteger(), nullable=False),
        sa.Column("user_id_2", sa.BigInteger(), nullable=False),
        sa.Column(
            "married_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("divorced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_marriages_user_id_1", "marriages", ["user_id_1"])
    op.create_index("ix_marriages_user_id_2", "marriages", ["user_id_2"])
    op.create_index("ix_marriages_active", "marriages", ["divorced_at"])

    # --- pending_actions ----------------------------------------------------
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("action_type", sa.String(length=16), nullable=False),
        sa.Column("initiator_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_pending_actions_initiator_id", "pending_actions", ["initiator_id"])
    op.create_index("ix_pending_actions_target_id", "pending_actions", ["target_id"])
    op.create_index(
        "ix_pending_target_status",
        "pending_actions",
        ["target_id", "status", "action_type"],
    )

    # --- treasures ----------------------------------------------------------
    op.create_table(
        "treasures",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("reward", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("claimed_by", sa.BigInteger(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "spawned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_treasures_chat_status", "treasures", ["chat_id", "status"])

    # --- scheduled_deletions ------------------------------------------------
    op.create_table(
        "scheduled_deletions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("done", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_deletions_pending", "scheduled_deletions", ["done", "delete_at"])


def downgrade() -> None:
    op.drop_table("scheduled_deletions")
    op.drop_table("treasures")
    op.drop_table("pending_actions")
    op.drop_table("marriages")
    op.drop_table("daily_nominations")
    op.drop_table("cooldowns")
    op.drop_table("transactions")
    op.drop_table("users")
