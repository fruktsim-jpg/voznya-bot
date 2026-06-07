"""Seed: полный каталог Telegram Gifts + Premium как позиции gift_catalog.

Данные-сид (НЕ схема). Дополняет ``gift_catalog`` (0018/0020) до ПОЛНОГО каталога
реальных Telegram Gifts из ``/gifts_available`` и заводит две позиции Telegram
Premium. Нужно для системы кейсов (CASES_REWORK_AUDIT_AND_PLAN.md, Этап 4 §2–3):
награды ``reward_kind='tg_gift'`` ссылаются на ``gift_catalog.code``.

Уже есть (0020): gift_heart, gift_bear, gift_rose, gift_rocket, gift_diamond.
Добавляем недостающие подарки и Premium:

    | code             | name        | star | eshki  | тир     |
    | gift_box         | Коробка     |  25  |   320  | T2      |
    | gift_cake        | Торт        |  50  |   700  | T3      |
    | gift_bouquet     | Букет       |  50  |   700  | T3      |
    | gift_champagne   | Шампанское  |  50  |   700  | T3      |
    | gift_cup         | Кубок       | 100  |  1450  | T4      |
    | gift_ring        | Кольцо      | 100  |  1450  | T4      |
    | gift_premium_3m  | Premium 3м  | 1000 | 10000  | premium |
    | gift_premium_6m  | Premium 6м  | 1500 | 15000  | premium |

``price_eshki`` — ценность подарка в ешках (используется аналитикой EV кейсов).
Premium: ценность зафиксирована (10000 / 15000 ешек), ``star_cost`` приблизителен
(meta.approx_star_cost=true), выдача — РУЧНАЯ через тот же конвейер
``GiftTransaction`` (pending → /gifts_done). ``stock=NULL`` — без лимитов
(линейка кейсов проектируется без лимитов; gift_catalog.stock к кейсам не
относится).

Идемпотентность: вставка «если ещё нет» по уникальному ``code``. ``downgrade``
удаляет ровно эти строки по кодам.

Revision ID: 0024_seed_full_gift_catalog
Revises: 0023_user_photo_url
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0024_seed_full_gift_catalog"
down_revision: Union[str, None] = "0023_user_photo_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (code, name, description, star_cost, price_eshki, sort_order, meta_sql)
_GIFTS = [
    ("gift_box", "Коробка", "Подарочная коробка. Средний тир.", 25, 320, 25, "NULL"),
    ("gift_cake", "Торт", "Праздничный торт. Дорогой тир.", 50, 700, 35, "NULL"),
    ("gift_bouquet", "Букет", "Букет цветов. Дорогой тир.", 50, 700, 36, "NULL"),
    ("gift_champagne", "Шампанское", "Бутылка шампанского. Дорогой тир.", 50, 700, 37, "NULL"),
    ("gift_cup", "Кубок", "Чемпионский кубок. Премиум-тир.", 100, 1450, 45, "NULL"),
    ("gift_ring", "Кольцо", "Кольцо. Премиум-тир.", 100, 1450, 46, "NULL"),
    (
        "gift_premium_3m",
        "Telegram Premium 3 месяца",
        "Подписка Telegram Premium на 3 месяца. Выдаётся вручную.",
        1000,
        10000,
        60,
        "'{\"approx_star_cost\": true, \"premium_months\": 3, \"manual\": true}'::jsonb",
    ),
    (
        "gift_premium_6m",
        "Telegram Premium 6 месяцев",
        "Подписка Telegram Premium на 6 месяцев. Выдаётся вручную.",
        1500,
        15000,
        61,
        "'{\"approx_star_cost\": true, \"premium_months\": 6, \"manual\": true}'::jsonb",
    ),
]

_CODES = tuple(g[0] for g in _GIFTS)


def upgrade() -> None:
    for code, name, desc, star, eshki, sort, meta in _GIFTS:
        op.execute(
            f"""
            INSERT INTO gift_catalog
                (code, name, description, star_cost, price_eshki,
                 telegram_gift_id, stock, reserved, sold_count,
                 is_active, sort_order, meta)
            SELECT
                '{code}', '{name}', '{desc}', {star}, {eshki},
                NULL, NULL, 0, 0, true, {sort}, {meta}
            WHERE NOT EXISTS (
                SELECT 1 FROM gift_catalog WHERE code = '{code}'
            )
            """
        )


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c in _CODES)
    op.execute(f"DELETE FROM gift_catalog WHERE code IN ({codes})")
