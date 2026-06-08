"""Денежные джекпоты + гарантия отсутствия лимитов на наградах кейсов.

Данные-правка (НЕ схема). По требованию продукта:

1. Денежные джекпоты: в каждом из 5 кейсов самая крупная денежная награда
   (currency с максимальным amount) помечается ``is_jackpot=true`` — чтобы
   крупный выигрыш ешек подавался как джекпот в UI (рядом с Premium 6м).

2. Никаких лимитов: у ВСЕХ наград кейсов ``max_global_supply=NULL`` (снимаем
   любые потолки на гифты/Premium/кейсы), ``granted_count`` обнуляем для
   чистоты. Кейсы открываются без ограничений по выпадению.

Идемпотентно и обратимо в разумных пределах: downgrade снимает jackpot с
currency-наград (gift_premium_6m остаётся джекпотом — это базовый сид 0025).

Revision ID: 0026_money_jackpots_no_limits
Revises: 0025_seed_five_cases
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0026_money_jackpots_no_limits"
down_revision: Union[str, None] = "0025_seed_five_cases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CASES = (
    "case_novice",
    "case_farmer",
    "case_hunter",
    "case_collector",
    "case_jackpot",
)


def upgrade() -> None:
    # 1. Снять все лимиты на наградах кейсов (никаких потолков выпадения).
    op.execute(
        """
        UPDATE case_rewards
           SET max_global_supply = NULL,
               granted_count = 0
         WHERE case_item_code IN (
             'case_novice','case_farmer','case_hunter',
             'case_collector','case_jackpot'
         )
        """
    )

    # 2. Денежный джекпот: самая крупная currency-награда в каждом кейсе.
    for code in _CASES:
        op.execute(
            f"""
            UPDATE case_rewards
               SET is_jackpot = true
             WHERE id = (
                 SELECT id FROM case_rewards
                  WHERE case_item_code = '{code}'
                    AND reward_kind = 'currency'
                  ORDER BY amount DESC NULLS LAST, id
                  LIMIT 1
             )
            """
        )


def downgrade() -> None:
    # Снять джекпот только с денежных наград (gift_premium_6m остаётся из 0025).
    op.execute(
        """
        UPDATE case_rewards
           SET is_jackpot = false
         WHERE reward_kind = 'currency'
           AND case_item_code IN (
               'case_novice','case_farmer','case_hunter',
               'case_collector','case_jackpot'
           )
        """
    )
