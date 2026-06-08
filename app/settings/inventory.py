"""Отображение инвентаря: редкости, иконки типов, тексты команды.

Единый источник правды по визуалу инвентаря в боте. Редкости и типы должны
совпадать с каталогом ``inventory_items`` (``ITEM_RARITIES`` / ``ITEM_TYPES``
в :mod:`app.models.inventory_item`) и с отображением на сайте — при изменении
держать в синхроне (как MMR-ранги, см. AGENTS.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RarityStyle:
    """Визуал редкости: эмодзи, человекочитаемое имя, порядок сортировки."""

    emoji: str
    name: str
    order: int  # чем выше — тем реже; для сортировки по убыванию


# Ключи совпадают с ITEM_RARITIES из каталога предметов.
RARITY_STYLES: dict[str, RarityStyle] = {
    "common": RarityStyle("⚪️", "Обычный", 1),
    "uncommon": RarityStyle("🟢", "Необычный", 2),
    "rare": RarityStyle("🔵", "Редкий", 3),
    "epic": RarityStyle("🟣", "Эпический", 4),
    "legendary": RarityStyle("🟠", "Легендарный", 5),
}

# Фолбэк для неизвестной редкости (мягкая деградация при рассинхроне каталога).
_FALLBACK_RARITY = RarityStyle("⚪️", "Обычный", 0)

# Иконки по типу предмета (ITEM_TYPES из каталога).
TYPE_EMOJI: dict[str, str] = {
    "cosmetic": "✨",
    "title": "🏷",
    "badge": "🎖",
    "frame": "🖼",
    "avatar": "👤",
    "collectible": "💎",
    "event": "🎉",
}


def rarity_style(rarity: str) -> RarityStyle:
    """Возвращает визуал редкости (с фолбэком на «Обычный»)."""
    return RARITY_STYLES.get(rarity, _FALLBACK_RARITY)


def type_emoji(item_type: str) -> str:
    """Возвращает иконку типа предмета (📦 — если тип неизвестен)."""
    return TYPE_EMOJI.get(item_type, "📦")


# --- Тексты команды инвентаря ------------------------------------------------
# Пагинация: сколько предметов на одной странице.
PAGE_SIZE = 10

INV_EMPTY = (
    "🎒 <b>Инвентарь {mention} пуст.</b>\n\n"
    "Предметы падают из кейсов и за события. Глянь <code>/кейсы</code>."
)


INV_HEADER = "🎒 <b>Инвентарь {mention}</b> — предметов: {count}"

# Строка предмета: «⚪️ ✨ Название ×2  (экипирован)».
INV_ROW = "{rarity_emoji} {type_emoji} <b>{name}</b>{qty}{equipped}"
INV_ROW_QTY = " ×{quantity}"
INV_ROW_EQUIPPED = " <i>(экипирован)</i>"
INV_ROW_DESC = "    <i>{description}</i>"

# --- Секция «Подарки и Premium» (pending Telegram Gifts) --------------------
# Подарки и Premium живут не в inventory, а в gift_transactions (pending) —
# это та же сущность, что показывает сайт. Показываем их отдельным блоком,
# чтобы инвентарь бота и сайта совпадали (единый источник правды).
INV_GIFTS_HEADER = "\n🎁 <b>Подарки и Premium</b> ({count}):"
# Строка подарка: «🎁 Роза — 250 🥚 (продать 175). Реши на сайте.»
INV_GIFTS_ROW = "🎁 <b>{name}</b> — {value} (продать {sell})"
INV_GIFTS_HINT = (
    "\n💡 Продать/вывести подарок: на сайте в разделе «Инвентарь» "
    "или прямо из сообщения о выпадении."
)

# Подвал с номером страницы (показывается только при пагинации).
INV_PAGE_FOOTER = "\nСтраница {page}/{pages}"


# Строка с количеством предметов для карточки профиля.
PROFILE_ITEMS_LINE = "🎒 Предметов: <b>{count}</b>\n"
