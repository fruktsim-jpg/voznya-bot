"""Deactivate the legacy keyed test case (case_vagabond).

``case_vagabond`` (migration 0017) was the first proof-of-concept case: it
``consumes_key=true`` (требует предмет-ключ). Утверждённая линейка V3 — это 5
кейсов из 0025, все открываются за ешки БЕЗ ключа. Старый кейс больше не нужен
и мешает: в витрине он лишний, а при открытии выдаёт «нужен ключ».

Это сид-правка данных (не схема): мягко выключаем кейс (``is_active=false``),
не удаляя историю открытий/дроп-лист. Откат включает его обратно.

Revision ID: 0027_deactivate_vagabond_case
Revises: 0026_money_jackpots_no_limits
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0027_deactivate_vagabond_case"
down_revision: Union[str, None] = "0026_money_jackpots_no_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE case_definitions SET is_active = false "
        "WHERE item_code = 'case_vagabond'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE case_definitions SET is_active = true "
        "WHERE item_code = 'case_vagabond'"
    )
