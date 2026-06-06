"""Seed: первый рабочий кейс «Кейс Бродяги» (case_vagabond).

Данные-сид (не схема): заводит первый РЕАЛЬНЫЙ кейс поверх Cases V1, чтобы
сразу проверить полный цикл (выдать → открыть → награда → леджер → сайт).

Создаёт:
1. Предмет каталога ``inventory_items`` ``case_vagabond`` (``type='case'``,
   ``stackable=true``) — без него open_case вернёт not_found, а админ-API не даст
   создать определение (требует type='case').
2. Определение ``case_definitions`` для ``case_vagabond``: открытие БЕСПЛАТНОЕ
   по ешкам, но списывает 1 ключ-кейс из инвентаря (``consumes_key=true``).
   Кейс — награда за активность, выдаётся игроку (админкой/будущими механиками).
3. Дроп-лист ``case_rewards`` — ЧИСТЫЕ ешки (V1, без косметики, т.к. каталог
   косметики пуст). Для currency-награды поле ``amount`` — фиксированная выплата
   (qty игнорируется кодом V1), поэтому «разброс» задаётся несколькими строками.

Дроп-лист и EV (Σ весов = 1000):
    |  ешки | вес | шанс | вклад в EV |
    |     5 | 350 |  35% |      1.75  |
    |    10 | 300 |  30% |      3.00  |
    |    20 | 200 |  20% |      4.00  |
    |    40 | 100 |  10% |      4.00  |
    |    75 |  40 | 4.0% |      3.00  |
    |   150 |  10 | 1.0% |      1.50  | (джекпот)
    EV ≈ 17.25 ешки за открытие. Кейс бесплатный (только ключ) → это кран
    (~треть дневной фермы за ключ), что нормально для наградного кейса.

Идемпотентность: вставки делаются «если ещё нет» (NOT EXISTS по коду), чтобы
повторный прогон/частичное состояние не падали на уникальных ограничениях.
``downgrade`` удаляет ровно эти строки по кодам (выданные игрокам кейсы и
историю открытий НЕ трогает — это пользовательские данные).

Revision ID: 0017_seed_vagabond_case
Revises: 0016_cases_foundation
Create Date: 2026-06-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017_seed_vagabond_case"
down_revision: Union[str, None] = "0016_cases_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CASE_CODE = "case_vagabond"

# (ешки, вес, джекпот)
_DROP = [
    (5, 350, False),
    (10, 300, False),
    (20, 200, False),
    (40, 100, False),
    (75, 40, False),
    (150, 10, True),
]


def upgrade() -> None:
    # 1. Предмет каталога (кейс как стековый предмет type='case').
    op.execute(
        """
        INSERT INTO inventory_items
            (code, type, slot, rarity, name, description,
             is_limited, max_supply, is_active, transferable, stackable)
        SELECT
            'case_vagabond', 'case', NULL, 'common',
            'Кейс Бродяги',
            'Уличный кейс за активность. Внутри — ешки, иногда заначка побольше.',
            false, NULL, true, false, true
        WHERE NOT EXISTS (
            SELECT 1 FROM inventory_items WHERE code = 'case_vagabond'
        )
        """
    )

    # 2. Определение кейса: бесплатно по ешкам, но списывает 1 ключ-кейс.
    op.execute(
        """
        INSERT INTO case_definitions
            (item_code, name, description, open_cost_kind, open_cost_amount,
             consumes_key, is_active)
        SELECT
            'case_vagabond', 'Кейс Бродяги',
            'Нашёл под скамейкой в Зволле. Открой и забери ешки.',
            'free', 0, true, true
        WHERE NOT EXISTS (
            SELECT 1 FROM case_definitions WHERE item_code = 'case_vagabond'
        )
        """
    )

    # 3. Дроп-лист (чистые ешки). Чистим возможные прежние строки этого кейса,
    #    чтобы сид был детерминирован, затем вставляем заново.
    op.execute("DELETE FROM case_rewards WHERE case_item_code = 'case_vagabond'")
    for amount, weight, is_jackpot in _DROP:
        op.execute(
            f"""
            INSERT INTO case_rewards
                (case_item_code, reward_kind, reward_item_code, amount, weight,
                 min_qty, max_qty, max_global_supply, granted_count, is_jackpot)
            VALUES
                ('case_vagabond', 'currency', NULL, {amount}, {weight},
                 1, 1, NULL, 0, {str(is_jackpot).lower()})
            """
        )


def downgrade() -> None:
    # Удаляем только сид-определение и его дроп-лист + предмет каталога.
    # Открытия (case_openings) и выданные игрокам кейсы (inventory) — это
    # пользовательские данные, их не трогаем.
    op.execute("DELETE FROM case_rewards WHERE case_item_code = 'case_vagabond'")
    op.execute("DELETE FROM case_definitions WHERE item_code = 'case_vagabond'")
    op.execute("DELETE FROM inventory_items WHERE code = 'case_vagabond'")
