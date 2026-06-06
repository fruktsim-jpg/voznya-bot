"""Рендеринг инвентаря игрока (текст для Telegram).

Только отображение: берём строки из ``repositories.inventory``, сортируем по
редкости (реже — выше), форматируем с эмодзи редкости/типа и собираем страницу.
Бизнес-логики владения здесь нет.
"""

from __future__ import annotations

from app.core.utils import mention
from app.repositories.inventory import InventoryRow
from app.settings import inventory as inv_texts


def _sort_rows(rows: list[InventoryRow]) -> list[InventoryRow]:
    """Сортирует: экипированные сверху, затем по редкости (реже выше), по имени.

    Сортировку по редкости делаем в Python (а не в SQL), потому что порядок
    задаётся справочником ``RARITY_STYLES.order``, а не алфавитом значения.
    """
    return sorted(
        rows,
        key=lambda r: (
            not r.equipped,                              # equipped → раньше
            -inv_texts.rarity_style(r.rarity).order,     # реже → выше
            r.name.lower(),
        ),
    )


def _format_row(row: InventoryRow) -> str:
    """Форматирует одну строку предмета (+ строку описания, если есть)."""
    style = inv_texts.rarity_style(row.rarity)
    line = inv_texts.INV_ROW.format(
        rarity_emoji=style.emoji,
        type_emoji=inv_texts.type_emoji(row.type),
        name=row.name,
        qty=inv_texts.INV_ROW_QTY.format(quantity=row.quantity)
        if row.quantity != 1
        else "",
        equipped=inv_texts.INV_ROW_EQUIPPED if row.equipped else "",
    )
    if row.description:
        line += "\n" + inv_texts.INV_ROW_DESC.format(description=row.description)
    return line


def render_inventory(
    rows: list[InventoryRow],
    total_count: int,
    *,
    user_id: int,
    first_name: str | None,
    username: str | None,
    page: int = 1,
    pages: int = 1,
) -> str:
    """Собирает текст инвентаря для одной страницы.

    ``rows`` — уже выбранная страница (или весь инвентарь, если пагинации нет);
    ``total_count`` — суммарное число предметов игрока (для заголовка).
    """
    who = mention(user_id, first_name, username)

    if total_count == 0:
        return inv_texts.INV_EMPTY.format(mention=who)

    parts = [inv_texts.INV_HEADER.format(mention=who, count=total_count), ""]
    parts.extend(_format_row(r) for r in _sort_rows(rows))

    if pages > 1:
        parts.append(inv_texts.INV_PAGE_FOOTER.format(page=page, pages=pages))

    return "\n".join(parts)
