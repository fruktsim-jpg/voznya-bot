"""Sync: проставить РЕАЛЬНЫЕ telegram_gift_id из нового источника истины.

Данные-сид (НЕ схема). Новый каталог Telegram Gifts (Release 2.2, site-first)
прислан как источник истины. Эта миграция привязывает реальные
``telegram_gift_id`` к УЖЕ существующим позициям ``gift_catalog``, которые
однозначно сопоставляются по эмодзи и номиналу Stars — чтобы авто-выдача
(deliver_gift → sendGift) работала без ручного /gifts_setid.

Сопоставление (9 обычных подарков, однозначно):

    code            эмодзи  star  telegram_gift_id
    gift_heart      ❤️      15    5170145012310081615
    gift_bear       🧸      15    5170233102089322756
    gift_box        🎁      25    5170250947678437525
    gift_rose       🌹      25    5168103777563050263
    gift_cake       🎂      50    5170144170496491616
    gift_bouquet    💐      50    5170314324215857265
    gift_rocket     🚀      50    5170564780938756245
    gift_champagne  🍾      50    6028601630662853006
    gift_ring       💍     100    5170690322832818290

НЕ трогаем здесь (требуют продуктового решения, отдельной миграцией):
* gift_cup (Кубок 🏆) и gift_diamond (Бриллиант 💎) — 100★, ОТСУТСТВУЮТ в новом
  списке → кандидаты на вывод из ассортимента (деактивация). Пока активны,
  чтобы не сломать дроп-листы кейсов (0025) до согласованного ре-сида кейсов.
* Два ⭐ 100★ (5168043875654172773, 5170521118301225164) и 8 «уникальных»
  подарков — НОВЫЕ позиции без названий/цен/редкости → требуют продуктового
  ввода (название, price_eshki, тир, участие в кейсах) до добавления.

Идемпотентно: UPDATE по code (только проставляет id, ничего не создаёт/удаляет).
downgrade обнуляет telegram_gift_id ровно у этих 9 кодов.

Revision ID: 0028_sync_gift_catalog_real_ids
Revises: 0027_deactivate_vagabond_case
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0028_sync_gift_catalog_real_ids"
down_revision: Union[str, None] = "0027_deactivate_vagabond_case"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (code, telegram_gift_id) — реальные id из нового источника истины.
_REAL_IDS = [
    ("gift_heart", "5170145012310081615"),
    ("gift_bear", "5170233102089322756"),
    ("gift_box", "5170250947678437525"),
    ("gift_rose", "5168103777563050263"),
    ("gift_cake", "5170144170496491616"),
    ("gift_bouquet", "5170314324215857265"),
    ("gift_rocket", "5170564780938756245"),
    ("gift_champagne", "6028601630662853006"),
    ("gift_ring", "5170690322832818290"),
]


def upgrade() -> None:
    for code, gid in _REAL_IDS:
        op.execute(
            f"UPDATE gift_catalog SET telegram_gift_id = '{gid}' WHERE code = '{code}'"
        )


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c, _ in _REAL_IDS)
    op.execute(
        f"UPDATE gift_catalog SET telegram_gift_id = NULL WHERE code IN ({codes})"
    )
