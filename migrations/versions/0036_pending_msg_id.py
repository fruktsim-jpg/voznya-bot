"""Store duel request message ids for cleanup.

Revision ID: 0036_pending_msg_id
Revises: 0035_seed_season_1
Create Date: 2026-06-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_pending_msg_id"
down_revision: Union[str, None] = "0035_seed_season_1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pending_actions",
        sa.Column("request_message_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pending_actions", "request_message_id")
