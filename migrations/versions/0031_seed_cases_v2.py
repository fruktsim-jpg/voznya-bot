"""Seed: Кейсы V2 — основная система кейсов (CASES_V2_DESIGN.md, APPROVED).

Данные-сид (НЕ схема). Полностью пересобирает линейку кейсов в V2:

* 6 кейсов: Новичок / Фармер / Охотник / Коллекционер / Premium / Джекпот;
* НОВЫЙ кейс ``case_premium`` (раньше Premium-кейса не было — линейка была из 5);
* старые цены/веса (0025) выводятся из эксплуатации: цены обновляются, дроп-листы
  пере-заливаются детерминированно (DELETE+INSERT по case_item_code);
* лимитки — КАЖДАЯ из 8 ID отдельной строкой награды (свой reward_item_code,
  вес, вероятность, статистика). ×2/×3 — та же лимитка с min_qty=max_qty=2/3;
* Premium только 3м/6м, поток снижен (фидбэк ревизии).

Экономика (источник истины): балансы/веса утверждены симуляцией
``scripts/cases_v2_sim.mjs``. Фактические RTP (Монте-Карло 1M):
  Новичок 0.945, Фармер 0.962, Охотник 0.887, Коллекционер 0.866,
  Premium 0.864, Джекпот 0.825 — все в утверждённых коридорах.

Лимитов нет: ``max_global_supply=NULL``. ``is_jackpot=true`` — только на топ
«Джекпота» (Premium 6м, лимитка ×3, денежный мега-приз 25000).

Старые кейсы 0025 (case_novice/farmer/hunter/collector/jackpot) переиспользуются
по тем же кодам — это и есть «миграция существующих на V2». ``case_premium``
заводится заново. Деактивированный «Бродяга» (0027) не трогаем.

Идемпотентно: предметы/определения — upsert; дроп-листы — DELETE+INSERT.

Revision ID: 0031_seed_cases_v2
Revises: 0030_fix_limited_catalog_canon
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0031_seed_cases_v2"
down_revision: Union[str, None] = "0030_fix_limited_catalog_canon"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 8 канонических лимиток (миграции 0029/0030).
_LIMITEDS = [
    "gift_xmas_bear",
    "gift_xmas_tree",
    "gift_valentine_bear",
    "gift_valentine_heart",
    "gift_spring_bear",
    "gift_lucky_bear",
    "gift_clown_bear",
    "gift_easter_bear",
]

# Определения кейсов V2: (code, name, rarity, price, description)
_CASES = [
    (
        "case_novice",
        "Кейс Новичка",
        "common",
        50,
        "Дешёвый первый азарт. В основном ешки, но микрошанс словить что угодно "
        "есть всегда.",
    ),
    (
        "case_farmer",
        "Кейс Фармера",
        "uncommon",
        150,
        "Лучший возврат ешек в линейке. Для тех, кто качает баланс каждый день.",
    ),
    (
        "case_hunter",
        "Кейс Охотника",
        "rare",
        400,
        "Максимальный шанс выбить обычный Telegram Gift. Охота за подарками.",
    ),
    (
        "case_collector",
        "Кейс Коллекционера",
        "epic",
        800,
        "Главный источник лимитированных подарков. Собери всех 8 сезонных мишек.",
    ),
    (
        "case_premium",
        "Кейс Premium",
        "epic",
        1500,
        "Лучшие шансы на Telegram Premium (3 и 6 месяцев). Высокий риск.",
    ),
    (
        "case_jackpot",
        "Кейс Джекпот",
        "legendary",
        2500,
        "Вершина системы: самые крупные выплаты, второй источник лимиток и "
        "денежный джекпот.",
    ),
]


def _limited_rows(per_weight: int, qty: int, jackpot: bool):
    """8 строк-наград (по одной на каждый ID) равного веса.

    qty>1 → та же лимитка кратностью (min_qty=max_qty=qty).
    """
    return [
        {
            "kind": "tg_gift",
            "code": code,
            "amount": None,
            "weight": per_weight,
            "qty": qty,
            "jackpot": jackpot,
        }
        for code in _LIMITEDS
    ]


def _cur(amount: int, weight: int, jackpot: bool = False):
    return {
        "kind": "currency",
        "code": None,
        "amount": amount,
        "weight": weight,
        "qty": 1,
        "jackpot": jackpot,
    }


def _gift(code: str, weight: int, jackpot: bool = False):
    return {
        "kind": "tg_gift",
        "code": code,
        "amount": None,
        "weight": weight,
        "qty": 1,
        "jackpot": jackpot,
    }


# Дроп-листы V2 (веса = симуляция cases_v2_sim.mjs).
_DROPS = {
    "case_novice": (
        [_cur(18, 3600), _cur(35, 2700), _cur(55, 1900), _cur(85, 1050), _cur(130, 450)]
        + [_gift("gift_heart", 250)]
        + _limited_rows(6, 1, False)
    ),
    "case_farmer": (
        [_cur(70, 2700), _cur(110, 3000), _cur(160, 2300), _cur(220, 1300), _cur(380, 470)]
        + [_gift("gift_bear", 90)]
        + _limited_rows(6, 1, False)
        + [_gift("gift_premium_3m", 5)]
    ),
    "case_hunter": (
        [_cur(200, 1850), _cur(320, 2600), _cur(550, 1700), _cur(1300, 432)]
        + [
            _gift("gift_heart", 1300),
            _gift("gift_box", 1200),
            _gift("gift_cake", 800),
            _gift("gift_ring", 380),
        ]
        + _limited_rows(6, 1, False)
        + [_gift("gift_premium_3m", 5)]
    ),
    "case_collector": (
        [_cur(200, 1400), _cur(420, 2700), _cur(700, 1900), _cur(1500, 700)]
        + [_gift("gift_ring", 900)]
        + _limited_rows(250, 1, False)
        + _limited_rows(40, 2, False)
        + [_gift("gift_premium_3m", 80)]
    ),
    "case_premium": (
        [_cur(450, 3000), _cur(900, 2950), _cur(1600, 2100), _cur(3500, 720)]
        + _limited_rows(113, 1, False)
        + [_gift("gift_premium_3m", 270), _gift("gift_premium_6m", 40)]
    ),
    "case_jackpot": (
        [_cur(700, 2980), _cur(1500, 2900), _cur(2500, 2100), _cur(6000, 820)]
        + _limited_rows(88, 1, False)
        + [
            _gift("gift_premium_3m", 260),
            _gift("gift_premium_6m", 70, jackpot=True),
        ]
        + _limited_rows(19, 3, True)
        + [_cur(25000, 20, jackpot=True)]
    ),
}

_CODES = [c[0] for c in _CASES]


def upgrade() -> None:
    for code, name, rarity, price, desc in _CASES:
        safe_desc = desc.replace("'", "''")
        safe_name = name.replace("'", "''")

        # 1. Предмет каталога (кейс как стековый предмет type='case').
        op.execute(
            f"""
            INSERT INTO inventory_items
                (code, type, slot, rarity, name, description,
                 is_limited, max_supply, is_active, transferable, stackable)
            SELECT
                '{code}', 'case', NULL, '{rarity}',
                '{safe_name}', '{safe_desc}',
                false, NULL, true, false, true
            WHERE NOT EXISTS (
                SELECT 1 FROM inventory_items WHERE code = '{code}'
            )
            """
        )

        # 2. Определение кейса: открытие за ешки, без ключа. Upsert + цена/имя.
        op.execute(
            f"""
            INSERT INTO case_definitions
                (item_code, name, description, open_cost_kind, open_cost_amount,
                 consumes_key, is_active)
            SELECT
                '{code}', '{safe_name}', '{safe_desc}', 'currency', {price},
                false, true
            WHERE NOT EXISTS (
                SELECT 1 FROM case_definitions WHERE item_code = '{code}'
            )
            """
        )
        # Обновляем цену/имя/описание/активность для уже существующих (миграция
        # старых кейсов 0025 на V2-цены).
        op.execute(
            f"""
            UPDATE case_definitions
               SET name = '{safe_name}',
                   description = '{safe_desc}',
                   open_cost_kind = 'currency',
                   open_cost_amount = {price},
                   consumes_key = false,
                   is_active = true
             WHERE item_code = '{code}'
            """
        )

        # 3. Дроп-лист: детерминированная пере-заливка.
        op.execute(f"DELETE FROM case_rewards WHERE case_item_code = '{code}'")
        for r in _DROPS[code]:
            kind = r["kind"]
            item_code = "NULL" if r["code"] is None else f"'{r['code']}'"
            amount = "NULL" if r["amount"] is None else str(r["amount"])
            qty = r["qty"]
            jackpot = "true" if r["jackpot"] else "false"
            op.execute(
                f"""
                INSERT INTO case_rewards
                    (case_item_code, reward_kind, reward_item_code, amount, weight,
                     min_qty, max_qty, max_global_supply, granted_count, is_jackpot)
                VALUES
                    ('{code}', '{kind}', {item_code}, {amount}, {r['weight']},
                     {qty}, {qty}, NULL, 0, {jackpot})
                """
            )


def downgrade() -> None:
    # Возврат невозможен «точно» (предыдущие веса 0025), поэтому просто чистим
    # дроп-листы V2 и деактивируем новый Premium-кейс. Старые кейсы остаются с
    # пустым дроп-листом — повторный прогон 0025 не предусмотрен (одноразовый
    # переход на V2). Для отката используйте восстановление из бэкапа.
    for code in _CODES:
        op.execute(f"DELETE FROM case_rewards WHERE case_item_code = '{code}'")
    op.execute(
        "UPDATE case_definitions SET is_active = false WHERE item_code = 'case_premium'"
    )
