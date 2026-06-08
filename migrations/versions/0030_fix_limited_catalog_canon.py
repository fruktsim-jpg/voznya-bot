"""Fix: привести лимитный каталог к каноническому источнику истины (Release 2.2 P0).

Данные-сид (НЕ схема). Источник истины по ЛИМИТНЫМ подаркам — ровно 8
telegram_gift_id (прислан как канон). Миграция 0029 по ошибке засидила ДЕВЯТУЮ
позицию ``gift_builder_bear`` (telegram_gift_id ``6026193266406327981``), которой
НЕТ в каноне → это не лимитный сезонный подарок.

Что делает миграция:

* Деактивирует ``gift_builder_bear`` (is_active=false): убираем из магазина,
  чтобы каталог совпадал с каноном 8 ID. Строку не удаляем (у кого-то мог уже
  оказаться pending) — просто выводим из ассортимента.
* Сверяет экономику 8 канонических лимиток: 50★, price_eshki = 1050
  (обычная 50★-цена 700 × 1.5), meta.limited/collectible/price_multiplier=1.5.
  0029 уже выставила эти значения; здесь UPDATE идемпотентно гарантирует их
  (на случай ручных правок/частичного прогона).

Канон 8 ID (лимитные):
    5956217000635139069  gift_xmas_bear
    5922558454332916696  gift_xmas_tree
    5800655655995968830  gift_valentine_bear
    5801108895304779062  gift_valentine_heart
    5866352046986232958  gift_spring_bear
    5893356958802511476  gift_lucky_bear
    5935895822435615975  gift_clown_bear
    5969796561943660080  gift_easter_bear

Идемпотентно. downgrade возвращает gift_builder_bear в активные.

Revision ID: 0030_fix_limited_catalog_canon
Revises: 0029_seed_collectible_gifts
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0030_fix_limited_catalog_canon"
down_revision: Union[str, None] = "0029_seed_collectible_gifts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Базовая цена обычного 50★-тира и множитель лимитки (см. 0029).
_LIMITED_STAR = 50
_LIMITED_ESHKI = 1050  # 700 × 1.5

# Канонические коды лимитных подарков (8 ID из источника истины).
_CANON_LIMITED_CODES = [
    "gift_xmas_bear",
    "gift_xmas_tree",
    "gift_valentine_bear",
    "gift_valentine_heart",
    "gift_spring_bear",
    "gift_lucky_bear",
    "gift_clown_bear",
    "gift_easter_bear",
]

# Ошибочно засиженная 0029 девятая позиция (НЕ в каноне).
_OFF_CANON_CODE = "gift_builder_bear"


def upgrade() -> None:
    # 1) Выводим из ассортимента не-каноническую «лимитку».
    op.execute(
        f"UPDATE gift_catalog SET is_active = false WHERE code = '{_OFF_CANON_CODE}'"
    )

    # 2) Идемпотентно фиксируем экономику и метки канонических лимиток.
    codes = ", ".join(f"'{c}'" for c in _CANON_LIMITED_CODES)
    op.execute(
        f"""
        UPDATE gift_catalog
           SET star_cost = {_LIMITED_STAR},
               price_eshki = {_LIMITED_ESHKI},
               is_active = true,
               meta = COALESCE(meta, '{{}}'::jsonb)
                      || '{{"limited": true, "collectible": true, "price_multiplier": 1.5}}'::jsonb
         WHERE code IN ({codes})
        """
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE gift_catalog SET is_active = true WHERE code = '{_OFF_CANON_CODE}'"
    )
