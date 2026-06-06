"""Seed: первый набор позиций gift_catalog (данные для тестирования экономики).

Данные-сид (НЕ схема): заводит первый реальный ассортимент Telegram Gifts поверх
``gift_catalog`` (0018), чтобы проверить витрину, себестоимость и Gifts-аналитику
Economic Control Center до запуска потока покупки.

Числа по сетке ``VOZNYA_ECONOMY_V2 §3`` (1★≈10 ешек, наценка +20–50%):

    | code         | name       | star | eshki | stock | тир      |
    | gift_heart   | Сердечко   |  15  |  200  |  50   | дешёвый  |
    | gift_bear    | Мишка      |  15  |  200  |  50   | дешёвый  |
    | gift_rose    | Роза       |  25  |  320  |  30   | средний  |
    | gift_rocket  | Ракета     |  50  |  700  |  15   | дорогой  |
    | gift_diamond | Бриллиант  | 100  | 1450  |   5   | премиум  |

Идемпотентность: вставка «если ещё нет» по уникальному ``code``. ``downgrade``
удаляет ровно эти строки по кодам (продажи/историю — нет, но потока покупки пока
нет, так что данных и не будет).

НЕ затрагивает схему. Только строки ``gift_catalog``.

Revision ID: 0020_seed_gift_catalog
Revises: 0019_item_ref_value
Create Date: 2026-06-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0020_seed_gift_catalog"
down_revision: Union[str, None] = "0019_item_ref_value"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (code, name, description, star_cost, price_eshki, stock, sort_order)
_GIFTS = [
    ("gift_heart", "Сердечко", "Дешёвый знак внимания. Массовый подарок.", 15, 200, 50, 10),
    ("gift_bear", "Мишка", "Плюшевый мишка. Тёплый и недорогой.", 15, 200, 50, 20),
    ("gift_rose", "Роза", "Классика. Средний тир.", 25, 320, 30, 30),
    ("gift_rocket", "Ракета", "Эффектный подарок для важного повода.", 50, 700, 15, 40),
    ("gift_diamond", "Бриллиант", "Премиум-статус. Редкая и дорогая вещь.", 100, 1450, 5, 50),
]

_CODES = tuple(g[0] for g in _GIFTS)


def upgrade() -> None:
    for code, name, desc, star, eshki, stock, sort in _GIFTS:
        op.execute(
            f"""
            INSERT INTO gift_catalog
                (code, name, description, star_cost, price_eshki,
                 telegram_gift_id, stock, reserved, sold_count,
                 is_active, sort_order)
            SELECT
                '{code}', '{name}', '{desc}', {star}, {eshki},
                NULL, {stock}, 0, 0, true, {sort}
            WHERE NOT EXISTS (
                SELECT 1 FROM gift_catalog WHERE code = '{code}'
            )
            """
        )


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c in _CODES)
    op.execute(f"DELETE FROM gift_catalog WHERE code IN ({codes})")
