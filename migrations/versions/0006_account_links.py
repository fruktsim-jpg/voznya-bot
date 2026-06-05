"""Привязка OIDC-аккаунтов Telegram Login к игрокам.

Создаёт две таблицы:

* ``account_links`` — подтверждённое соответствие OIDC ``sub`` → ``user_id``.
* ``oidc_link_requests`` — одноразовые запросы на привязку (token → sub, TTL).

Игровые таблицы (``users`` и др.) НЕ изменяются.

Revision ID: 0006_account_links
Revises: 0005_open_duels
Create Date: 2026-06-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_account_links"
down_revision: Union[str, None] = "0005_open_duels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_links",
        sa.Column("oidc_sub", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("oidc_sub"),
    )
    op.create_index(
        op.f("ix_account_links_user_id"),
        "account_links",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "oidc_link_requests",
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("oidc_sub", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index(
        op.f("ix_oidc_link_requests_expires_at"),
        "oidc_link_requests",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_oidc_link_requests_expires_at"),
        table_name="oidc_link_requests",
    )
    op.drop_table("oidc_link_requests")
    op.drop_index(
        op.f("ix_account_links_user_id"),
        table_name="account_links",
    )
    op.drop_table("account_links")
