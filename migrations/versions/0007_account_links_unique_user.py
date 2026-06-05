"""Биекция account_links: один user_id ↔ один oidc_sub.

До этой миграции таблица ``account_links`` гарантировала уникальность только
по ``oidc_sub`` (первичный ключ). Один Telegram ``user_id`` мог оказаться
привязан к нескольким OIDC ``sub`` (двойная привязка). Делаем связь строго
взаимно-однозначной:

* подчищаем возможные дубли по ``user_id`` (оставляем самую раннюю связь);
* убираем старый НЕуникальный индекс ``ix_account_links_user_id``;
* добавляем UNIQUE-ограничение ``uq_account_links_user_id`` (оно создаёт свой
  уникальный индекс, который обслуживает и обратный поиск «sub по user_id»).

Игровые таблицы не затрагиваются.

Revision ID: 0007_account_links_unique_user
Revises: 0006_account_links
Create Date: 2026-06-06

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007_account_links_unique_user"
down_revision: Union[str, None] = "0006_account_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # (1) Дедупликация: если один user_id привязан к нескольким sub, оставляем
    # самую раннюю связь (по created_at, при равенстве — по oidc_sub). Лишние
    # удаляем, иначе UNIQUE-ограничение не создастся. На проде дублей быть не
    # должно (PK по oidc_sub + флоу через бота), но миграция обязана быть
    # идемпотентной и безопасной.
    op.execute(
        """
        DELETE FROM account_links a
        USING account_links b
        WHERE a.user_id = b.user_id
          AND (
                a.created_at > b.created_at
             OR (a.created_at = b.created_at AND a.oidc_sub > b.oidc_sub)
          )
        """
    )

    # (2) Старый неуникальный индекс больше не нужен — его роль возьмёт на себя
    # уникальный индекс под ограничением.
    op.drop_index("ix_account_links_user_id", table_name="account_links")

    # (3) Уникальность user_id на уровне БД.
    op.create_unique_constraint(
        "uq_account_links_user_id", "account_links", ["user_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_account_links_user_id", "account_links", type_="unique"
    )
    op.create_index(
        op.f("ix_account_links_user_id"),
        "account_links",
        ["user_id"],
        unique=False,
    )
