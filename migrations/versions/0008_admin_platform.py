"""Фундамент админ-платформы: admin_roles + audit_log.

Создаёт две базовые таблицы платформы администрирования:

* ``admin_roles`` — назначение игроку роли (owner/admin/moderator/support);
* ``audit_log`` — неизменяемая лента всех административных действий.

Игровые таблицы, экономика и привязки (account_links / oidc_link_requests) не
затрагиваются. Магазин и инвентарь здесь НЕ создаются — это отдельный этап
(см. ``ADMIN_PLATFORM.md`` → roadmap).

Revision ID: 0008_admin_platform
Revises: 0007_account_links_unique_user
Create Date: 2026-06-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_admin_platform"
down_revision: Union[str, None] = "0007_account_links_unique_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- admin_roles ---------------------------------------------------------
    op.create_table(
        "admin_roles",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=True),
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
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        op.f("ix_admin_roles_role"), "admin_roles", ["role"], unique=False
    )

    # --- audit_log -----------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_role", sa.String(length=16), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_log_actor", "audit_log", ["actor_user_id", "created_at"]
    )
    op.create_index(
        "ix_audit_log_target", "audit_log", ["target_user_id", "created_at"]
    )
    op.create_index(
        "ix_audit_log_action", "audit_log", ["action", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_target", table_name="audit_log")
    op.drop_index("ix_audit_log_actor", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index(op.f("ix_admin_roles_role"), table_name="admin_roles")
    op.drop_table("admin_roles")
