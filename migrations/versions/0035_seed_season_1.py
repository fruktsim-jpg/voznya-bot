"""Seed: сезонный кейс «Сезон 1» (Сезон 1, APPROVED).

Данные-сид (НЕ схема). Заводит ОДИН сезонный кейс ``case_season_1``:
* открытие за ешки (цена из app/settings/season.py — SEASON_CASE_PRICE=600);
* дроп-лист с RTP<1 (сток сохраняется): ешки + обычные подарки + 8 канонических
  лимиток как «сезонный пул». Веса подобраны под RTP ≈ 0.88.

Сезонные лимитки на старте Сезона 1 переиспользуют 8 канонических лимиток
(миграции 0029/0030) — отдельные item_code заводить не нужно, чтобы не плодить
фейковые Telegram-ID. Их «несезонная продажа» закрыта в логике sell (sell-rate),
а доступны они теперь дополнительно через сезонный кейс.

Идемпотентно: предмет/определение — upsert; дроп-лист — DELETE+INSERT.

Revision ID: 0035_seed_season_1
Revises: 0034_season_1_wipe
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0035_seed_season_1"
down_revision: Union[str, None] = "0034_season_1_wipe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CODE = "case_season_1"
_NAME = "Кейс Сезона 1"
_PRICE = 600
_DESC = (
    "Сезонный кейс Сезона 1. Единственный источник сезонного пула лимиток. "
    "Доступен только в этом сезоне."
)

# 8 канонических лимиток (миграции 0029/0030) — сезонный пул.
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


def _cur(amount: int, weight: int):
    return ("currency", None, amount, weight, 1, False)


def _gift(code: str, weight: int, jackpot: bool = False):
    return ("tg_gift", code, None, weight, 1, jackpot)


def _limited_rows(per_weight: int):
    return [_gift(code, per_weight) for code in _LIMITEDS]


# Дроп-лист сезонного кейса (цена 600). Подобран под RTP ≈ 0.88: основная масса —
# умеренный возврат ешками, обычные подарки и редкий сезонный пул лимиток.
_DROPS = (
    [
        _cur(200, 2600),
        _cur(380, 2900),
        _cur(620, 1800),
        _cur(1200, 600),
    ]
    + [_gift("gift_ring", 700)]
    + _limited_rows(120)
    + [_gift("gift_premium_3m", 40)]
)


def upgrade() -> None:
    safe_name = _NAME.replace("'", "''")
    safe_desc = _DESC.replace("'", "''")

    # 1. Предмет каталога (кейс как стековый предмет type='case').
    op.execute(
        f"""
        INSERT INTO inventory_items
            (code, type, slot, rarity, name, description,
             is_limited, max_supply, is_active, transferable, stackable)
        SELECT
            '{_CODE}', 'case', NULL, 'epic',
            '{safe_name}', '{safe_desc}',
            false, NULL, true, false, true
        WHERE NOT EXISTS (
            SELECT 1 FROM inventory_items WHERE code = '{_CODE}'
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
            '{_CODE}', '{safe_name}', '{safe_desc}', 'currency', {_PRICE},
            false, true
        WHERE NOT EXISTS (
            SELECT 1 FROM case_definitions WHERE item_code = '{_CODE}'
        )
        """
    )
    op.execute(
        f"""
        UPDATE case_definitions
           SET name = '{safe_name}',
               description = '{safe_desc}',
               open_cost_kind = 'currency',
               open_cost_amount = {_PRICE},
               consumes_key = false,
               is_active = true
         WHERE item_code = '{_CODE}'
        """
    )

    # 3. Дроп-лист: детерминированная пере-заливка.
    op.execute(f"DELETE FROM case_rewards WHERE case_item_code = '{_CODE}'")
    for kind, code, amount, weight, qty, jackpot in _DROPS:
        item_code = "NULL" if code is None else f"'{code}'"
        amount_sql = "NULL" if amount is None else str(amount)
        jackpot_sql = "true" if jackpot else "false"
        op.execute(
            f"""
            INSERT INTO case_rewards
                (case_item_code, reward_kind, reward_item_code, amount, weight,
                 min_qty, max_qty, max_global_supply, granted_count, is_jackpot)
            VALUES
                ('{_CODE}', '{kind}', {item_code}, {amount_sql}, {weight},
                 {qty}, {qty}, NULL, 0, {jackpot_sql})
            """
        )


def downgrade() -> None:
    op.execute(f"DELETE FROM case_rewards WHERE case_item_code = '{_CODE}'")
    op.execute(
        f"UPDATE case_definitions SET is_active = false WHERE item_code = '{_CODE}'"
    )
