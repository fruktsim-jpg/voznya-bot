"""Seed: лимитные сезонные collectible-подарки (новый источник истины).

Данные-сид (НЕ схема). Добавляет 9 лимитных сезонных Telegram Gifts (все 50★),
присланных как часть нового каталога. Это коллекционные подарки с реальными
``telegram_gift_id`` → авто-выдача работает сразу.

Экономика: 50★ → price_eshki 700 (тот же тир, что Торт/Букет/Шампанское,
star×10×1.4), sell_value = floor(700×0.70) = 490. Флаг лимитности в meta
(``limited=true``, ``collectible=true``, ``season``) — для бейджа на сайте и
повышенной редкости в UI. ``stock=NULL`` (как у остального каталога; реальная
доступность у Telegram сезонная, явный лимит пула здесь не вводим).

В кейсы НЕ добавляются этой миграцией — участие в дроп-листах и пересчёт весов
100★/50★ тира требуют отдельного согласованного ре-сида кейсов
(см. docs/RELEASE_2_2_GIFT_CATALOG_REVISION.md §5).

Идемпотентно: INSERT «если ещё нет» по code. downgrade удаляет ровно эти коды.

Revision ID: 0029_seed_collectible_gifts
Revises: 0028_sync_gift_catalog_real_ids
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0029_seed_collectible_gifts"
down_revision: Union[str, None] = "0028_sync_gift_catalog_real_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STAR = 50
_ESHKI = 700

# (code, name, season, telegram_gift_id, sort_order)
_GIFTS = [
    ("gift_xmas_tree", "Рождественская ёлка", "Декабрь 2025", "5922558454332916696", 70),
    ("gift_xmas_bear", "Рождественский мишка", "Декабрь 2025", "5956217000635139069", 71),
    ("gift_valentine_bear", "Валентинов мишка", "Февраль 2026", "5800655655995968830", 72),
    ("gift_valentine_heart", "Сердце с цветами", "Февраль 2026", "5801108895304779062", 73),
    ("gift_spring_bear", "Весенний мишка", "8 марта 2026", "5866352046986232958", 74),
    ("gift_lucky_bear", "Счастливый мишка", "День святого Патрика 2026", "5893356958802511476", 75),
    ("gift_clown_bear", "Мишка-клоун", "1 апреля 2026", "5935895822435615975", 76),
    ("gift_easter_bear", "Пасхальный мишка", "Апрель 2026", "5969796561943660080", 77),
    ("gift_builder_bear", "Мишка-строитель", "Май 2026", "6026193266406327981", 78),
]

_CODES = tuple(g[0] for g in _GIFTS)


def upgrade() -> None:
    for code, name, season, gid, sort in _GIFTS:
        desc = f"Лимитный сезонный подарок ({season}). Коллекционный, 50★."
        meta = (
            "'{\"limited\": true, \"collectible\": true, "
            f"\"season\": \"{season}\""
            "}'::jsonb"
        )
        op.execute(
            f"""
            INSERT INTO gift_catalog
                (code, name, description, star_cost, price_eshki,
                 telegram_gift_id, stock, reserved, sold_count,
                 is_active, sort_order, meta)
            SELECT
                '{code}', '{name}', '{desc}', {_STAR}, {_ESHKI},
                '{gid}', NULL, 0, 0, true, {sort}, {meta}
            WHERE NOT EXISTS (
                SELECT 1 FROM gift_catalog WHERE code = '{code}'
            )
            """
        )


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c in _CODES)
    op.execute(f"DELETE FROM gift_catalog WHERE code IN ({codes})")
