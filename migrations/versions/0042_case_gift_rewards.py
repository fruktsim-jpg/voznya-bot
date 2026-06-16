"""Бэкфилл: награды-подарки в кейсах → reward_kind='tg_gift'.

Раньше подарок, добавленный в кейс через админку, сохранялся как
``reward_kind='item'`` с ``reward_item_code`` → ``inventory_items`` (type='gift').
При открытии такой дроп падал в стековый ``inventory`` как обычный предмет —
его НЕЛЬЗЯ было ни продать, ни вывести (sell/withdraw работают только с
``gift_transactions``), и его стоимость не учитывалась в RTP.

Правильный вид для подарка — ``reward_kind='tg_gift'`` (выдаётся как Telegram-
подарок: можно оставить / продать / вывести; ценится по ``gift_catalog``).
Эта миграция конвертирует уже существующие записи: любая ``case_rewards`` с
``reward_kind='item'``, чей ``reward_item_code`` указывает и на gift-предмет
(``inventory_items.type='gift'``), и присутствует в ``gift_catalog``, становится
``tg_gift``. ``reward_item_code`` сохраняется (для tg_gift он указывает на
``gift_catalog.code`` — код общий с inventory_items по контракту Gift Studio).

Идемпотентно и безопасно: трогает только подходящие строки. Прошлые открытия
(``case_openings``) хранят ``weight_snapshot`` и не переписываются.

Revision ID: 0042_case_gift_rewards
Revises: 0041_moderation
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0042_case_gift_rewards"
down_revision: Union[str, None] = "0041_moderation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # item → tg_gift для наград, которые на самом деле подарки.
    op.execute(
        """
        UPDATE case_rewards r
           SET reward_kind = 'tg_gift'
          FROM inventory_items i
         WHERE r.reward_item_code = i.code
           AND r.reward_kind = 'item'
           AND i.type = 'gift'
           AND EXISTS (
                 SELECT 1 FROM gift_catalog g WHERE g.code = r.reward_item_code
               )
        """
    )


def downgrade() -> None:
    # Обратная конвертация tg_gift → item для тех же gift-наград. Безопасно,
    # т.к. до этой миграции такие подарки и хранились как item.
    op.execute(
        """
        UPDATE case_rewards r
           SET reward_kind = 'item'
          FROM inventory_items i
         WHERE r.reward_item_code = i.code
           AND r.reward_kind = 'tg_gift'
           AND i.type = 'gift'
        """
    )
