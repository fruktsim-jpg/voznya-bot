"""users.photo_url — Telegram-аватарка игрока (MVP).

Аддитивная nullable-колонка под URL аватарки из Telegram. Заполняется при входе
на сайте (Login Widget `photo_url` / OIDC `picture`) — это единственный момент,
когда Telegram отдаёт публичный URL фото. Бот в обычных сообщениях URL фото не
получает, поэтому колонка может оставаться NULL у тех, кто не логинился на сайте
(UI откатывается на инициал). Сайт пишет ТОЛЬКО эту колонку и ТОЛЬКО UPDATE'ом
существующих строк — он не создаёт пользователей (их создаёт бот).

НЕ затрагивает другие колонки/таблицы.

Revision ID: 0023_user_photo_url
Revises: 0022_stars_ledger
Create Date: 2026-06-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_user_photo_url"
down_revision: Union[str, None] = "0022_stars_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("photo_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "photo_url")
