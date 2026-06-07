"""Seed: 5 кейсов линейки (Новичок/Фармер/Охотник/Коллекционер/Джекпот).

Данные-сид (НЕ схема). Заводит утверждённую линейку кейсов по
``CASES_REWORK_AUDIT_AND_PLAN.md`` (v3, Этап 2). Для каждого кейса:

1. ``inventory_items`` (type='case', stackable) — предмет-кейс;
2. ``case_definitions`` — открытие за ешки (``open_cost_kind='currency'``),
   БЕЗ ключа (``consumes_key=false``): кейс покупается прямо за баланс;
3. ``case_rewards`` — дроп-лист: по строке на каждый номинал ешек и на КАЖДЫЙ
   из 11 Telegram Gifts + оба Premium. Σ весов каждого кейса = 100000.

Лимитов нет: ``max_global_supply=NULL`` у всех наград (по утверждённому решению).
Премиум-награды (gift_premium_6m) помечены ``is_jackpot=true`` для подачи в UI.

Цены и веса — строго из утверждённых таблиц (не менять без необходимости).
Идемпотентность: предметы и определения — «если ещё нет»; дроп-лист каждого кейса
пере-заливается детерминированно (DELETE+INSERT по case_item_code).

Revision ID: 0025_seed_five_cases
Revises: 0024_seed_full_gift_catalog
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0025_seed_five_cases"
down_revision: Union[str, None] = "0024_seed_full_gift_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Определения кейсов: (code, name, rarity, price, description)
_CASES = [
    (
        "case_novice",
        "Кейс Новичка",
        "common",
        10,
        "Дешёвый лотерейный билет. В основном ешки, но шанс словить что угодно "
        "есть всегда — вплоть до Premium.",
    ),
    (
        "case_farmer",
        "Кейс Фармера",
        "uncommon",
        30,
        "Лучший возврат ешек в линейке. Для тех, кто качает баланс.",
    ),
    (
        "case_hunter",
        "Кейс Охотника",
        "rare",
        75,
        "Лучший шанс выбить реальный Telegram Gift. Охота за подарками.",
    ),
    (
        "case_collector",
        "Кейс Коллекционера",
        "epic",
        200,
        "Статусные подарки и реальный шанс на Telegram Premium.",
    ),
    (
        "case_jackpot",
        "Кейс Джекпот",
        "legendary",
        500,
        "Вершина системы: самые жирные подарки и лучший шанс на Premium.",
    ),
]

# Дроп-листы. Для каждого кейса: currency = [(amount, weight)],
# gifts = [(gift_code, weight)]. Σ весов = 100000.
_DROPS = {
    "case_novice": {
        "currency": [(3, 34000), (6, 30000), (10, 20000), (18, 10000), (40, 4000), (100, 1500)],
        "gifts": [
            ("gift_heart", 150), ("gift_bear", 150),
            ("gift_box", 60), ("gift_rose", 60),
            ("gift_cake", 12), ("gift_bouquet", 12), ("gift_rocket", 12), ("gift_champagne", 12),
            ("gift_cup", 6), ("gift_ring", 6), ("gift_diamond", 6),
            ("gift_premium_3m", 10), ("gift_premium_6m", 4),
        ],
    },
    "case_farmer": {
        "currency": [(10, 32000), (18, 30000), (30, 21000), (50, 10000), (110, 5000), (280, 1200)],
        "gifts": [
            ("gift_heart", 250), ("gift_bear", 250),
            ("gift_box", 100), ("gift_rose", 100),
            ("gift_cake", 16), ("gift_bouquet", 16), ("gift_rocket", 16), ("gift_champagne", 16),
            ("gift_cup", 8), ("gift_ring", 8), ("gift_diamond", 8),
            ("gift_premium_3m", 8), ("gift_premium_6m", 4),
        ],
    },
    "case_hunter": {
        "currency": [(25, 30000), (45, 28000), (75, 17000), (140, 8000)],
        "gifts": [
            ("gift_heart", 4000), ("gift_bear", 4000),
            ("gift_box", 2500), ("gift_rose", 2500),
            ("gift_cake", 625), ("gift_bouquet", 625), ("gift_rocket", 625), ("gift_champagne", 625),
            ("gift_cup", 400), ("gift_ring", 400), ("gift_diamond", 400),
            ("gift_premium_3m", 250), ("gift_premium_6m", 50),
        ],
    },
    "case_collector": {
        "currency": [(70, 28000), (130, 24000), (220, 16000), (450, 7000)],
        "gifts": [
            ("gift_heart", 1000), ("gift_bear", 1000),
            ("gift_box", 4000), ("gift_rose", 4000),
            ("gift_cake", 1750), ("gift_bouquet", 1750), ("gift_rocket", 1750), ("gift_champagne", 1750),
            ("gift_cup", 2000), ("gift_ring", 2000), ("gift_diamond", 2000),
            ("gift_premium_3m", 1500), ("gift_premium_6m", 500),
        ],
    },
    "case_jackpot": {
        "currency": [(200, 26000), (350, 26000), (600, 16000), (1100, 7000)],
        "gifts": [
            ("gift_heart", 500), ("gift_bear", 500),
            ("gift_box", 1000), ("gift_rose", 1000),
            ("gift_cake", 1500), ("gift_bouquet", 1500), ("gift_rocket", 1500), ("gift_champagne", 1500),
            ("gift_cup", 3000), ("gift_ring", 3000), ("gift_diamond", 3000),
            ("gift_premium_3m", 5000), ("gift_premium_6m", 2000),
        ],
    },
}

# Топ-приз для подачи джекпота в UI.
_JACKPOT_GIFT = "gift_premium_6m"

_CODES = [c[0] for c in _CASES]


def upgrade() -> None:
    for code, name, rarity, price, desc in _CASES:
        # 1. Предмет каталога (кейс как стековый предмет type='case').
        op.execute(
            f"""
            INSERT INTO inventory_items
                (code, type, slot, rarity, name, description,
                 is_limited, max_supply, is_active, transferable, stackable)
            SELECT
                '{code}', 'case', NULL, '{rarity}',
                '{name}', '{desc}',
                false, NULL, true, false, true
            WHERE NOT EXISTS (
                SELECT 1 FROM inventory_items WHERE code = '{code}'
            )
            """
        )

        # 2. Определение кейса: открытие за ешки, без ключа.
        op.execute(
            f"""
            INSERT INTO case_definitions
                (item_code, name, description, open_cost_kind, open_cost_amount,
                 consumes_key, is_active)
            SELECT
                '{code}', '{name}', '{desc}', 'currency', {price},
                false, true
            WHERE NOT EXISTS (
                SELECT 1 FROM case_definitions WHERE item_code = '{code}'
            )
            """
        )

        # 3. Дроп-лист: детерминированная пере-заливка.
        op.execute(f"DELETE FROM case_rewards WHERE case_item_code = '{code}'")

        drop = _DROPS[code]
        for amount, weight in drop["currency"]:
            op.execute(
                f"""
                INSERT INTO case_rewards
                    (case_item_code, reward_kind, reward_item_code, amount, weight,
                     min_qty, max_qty, max_global_supply, granted_count, is_jackpot)
                VALUES
                    ('{code}', 'currency', NULL, {amount}, {weight},
                     1, 1, NULL, 0, false)
                """
            )
        for gift_code, weight in drop["gifts"]:
            is_jackpot = "true" if gift_code == _JACKPOT_GIFT else "false"
            op.execute(
                f"""
                INSERT INTO case_rewards
                    (case_item_code, reward_kind, reward_item_code, amount, weight,
                     min_qty, max_qty, max_global_supply, granted_count, is_jackpot)
                VALUES
                    ('{code}', 'tg_gift', '{gift_code}', NULL, {weight},
                     1, 1, NULL, 0, {is_jackpot})
                """
            )


def downgrade() -> None:
    for code in _CODES:
        op.execute(f"DELETE FROM case_rewards WHERE case_item_code = '{code}'")
        op.execute(f"DELETE FROM case_definitions WHERE item_code = '{code}'")
        op.execute(f"DELETE FROM inventory_items WHERE code = '{code}'")
