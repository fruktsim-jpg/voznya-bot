"""Item reference value: inventory_items.ref_value (для аналитики EV/P&L).

Добавляет одну nullable-колонку ``inventory_items.ref_value`` — оценочную
стоимость предмета в ешках. Это СПРАВОЧНОЕ поле для аналитики: позволяет считать
полный EV кейсов (валюта + предметы) и оценивать предметные награды/подарки.
NULL = предмет не оценён (в EV не участвует).

НЕ затрагивает: users, баланс, transactions(структуру), inventory/
inventory_history(структуру), case_*(структуру), admin_roles, audit_log, OIDC,
account_links. Это не цена покупки и не трогает экономическое ядро.

Revision ID: 0019_item_ref_value
Revises: 0018_gift_catalog
Create Date: 2026-06-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_item_ref_value"
down_revision: Union[str, None] = "0018_gift_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inventory_items",
        sa.Column("ref_value", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_inventory_items_ref_value_nonneg",
        "inventory_items",
        "ref_value IS NULL OR ref_value >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_inventory_items_ref_value_nonneg",
        "inventory_items",
        type_="check",
    )
    op.drop_column("inventory_items", "ref_value")
