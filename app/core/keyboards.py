"""Инлайн-клавиатуры (кнопки) для ускорения взаимодействия.

Callback-данные имеют единый формат ``<feature>:<action>:<args...>`` и
проверяются в соответствующих обработчиках (защита от чужих нажатий,
повторов и просрочки).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def quick_actions() -> InlineKeyboardMarkup:
    """Быстрые кнопки для частых действий."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💊 Ферма", callback_data="quick:farm")
    builder.button(text="💰 Баланс", callback_data="quick:balance")
    builder.button(text="🏅 Ачивки", callback_data="quick:achievements")
    builder.adjust(3)
    return builder.as_markup()


def duel_accept(pending_id: int) -> InlineKeyboardMarkup:
    """Кнопка принятия дуэли."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Принять бой", callback_data=f"duel:accept:{pending_id}")]
        ]
    )


def marriage_accept(pending_id: int) -> InlineKeyboardMarkup:
    """Кнопки согласия или отказа на брак."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💍 Согласиться", callback_data=f"marry:accept:{pending_id}"),
                InlineKeyboardButton(text="❌ Отказать", callback_data=f"marry:decline:{pending_id}")
            ]
        ]
    )


def casino_again(user_id: int, bet: int) -> InlineKeyboardMarkup:
    """Кнопка повтора ставки в казино + быстрые действия."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🎰 Повторить ставку", callback_data=f"casino:repeat:{user_id}:{bet}")
    builder.button(text="💰 Баланс", callback_data="quick:balance")
    builder.button(text="👤 Профиль", callback_data="quick:profile")
    builder.button(text="🏅 Ачивки", callback_data="quick:achievements")
    builder.adjust(1, 3)
    return builder.as_markup()


def top_pagination(page: int, total_pages: int, user_id: int) -> InlineKeyboardMarkup:
    """Кнопки пагинации для топа."""
    buttons = []
    
    # Кнопка "Назад"
    if page > 1:
        buttons.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"top:page:{page - 1}:{user_id}"
            )
        )
    
    # Кнопка "Вперёд"
    if page < total_pages:
        buttons.append(
            InlineKeyboardButton(
                text="▶️ Вперёд",
                callback_data=f"top:page:{page + 1}:{user_id}"
            )
        )
    
    return InlineKeyboardMarkup(inline_keyboard=[buttons] if buttons else [])


def achievements_full_button(user_id: int) -> InlineKeyboardMarkup:
    """Кнопка для показа всех достижений."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📖 Все достижения", callback_data=f"ach:full:{user_id}")]
        ]
    )


def divorce_confirm(user_id: int, partner_id: int) -> InlineKeyboardMarkup:
    """Кнопки подтверждения развода."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💔 Да, расстаться",
                    callback_data=f"divorce:confirm:{user_id}:{partner_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"divorce:cancel:{user_id}"
                )
            ]
        ]
    )
