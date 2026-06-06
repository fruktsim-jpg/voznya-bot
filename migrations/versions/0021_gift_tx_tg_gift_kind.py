"""gift_transactions: разрешить kind='tg_gift' (реальный Telegram Gift).

Реальный Telegram Gift — внешний актив (выдаётся через Bot API ``sendGift``), не
предмет инвентаря и не ешки. Расширяем CHECK ``ck_gift_kind_payload``, чтобы
``kind='tg_gift'`` был допустим с ``item_code`` = код подарка из ``gift_catalog``.

Жизненный цикл такой доставки: ``status`` pending→completed/cancelled,
идемпотентность через ``idempotency_key``, себестоимость в Stars и данные
Telegram — в ``meta`` (charge_id Telegram НЕ возвращает, см. TELEGRAM_GIFTS_AUDIT).

НЕ затрагивает: данные, другие таблицы, экономическое ядро. Только пересоздаёт
один CHECK-констрейнт на ``gift_transactions``.

Revision ID: 0021_gift_tx_tg_gift_kind
Revises: 0020_seed_gift_catalog
Create Date: 2026-06-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0021_gift_tx_tg_gift_kind"
down_revision: Union[str, None] = "0020_seed_gift_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = (
    "(kind = 'item' AND item_code IS NOT NULL) OR "
    "(kind = 'currency' AND amount IS NOT NULL)"
)
_NEW = (
    "(kind = 'item' AND item_code IS NOT NULL) OR "
    "(kind = 'currency' AND amount IS NOT NULL) OR "
    "(kind = 'tg_gift' AND item_code IS NOT NULL)"
)


def upgrade() -> None:
    op.drop_constraint("ck_gift_kind_payload", "gift_transactions", type_="check")
    op.create_check_constraint(
        "ck_gift_kind_payload", "gift_transactions", _NEW
    )


def downgrade() -> None:
    # Откат: только если нет строк с новым видом (иначе CHECK не создастся).
    op.execute("DELETE FROM gift_transactions WHERE kind = 'tg_gift'")
    op.drop_constraint("ck_gift_kind_payload", "gift_transactions", type_="check")
    op.create_check_constraint(
        "ck_gift_kind_payload", "gift_transactions", _OLD
    )
