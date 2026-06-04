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
    builder.button(text="👤 Профиль", callback_data="quick:profile")
    builder.button(text="🏅 Ачивки", callback_data="quick:achievements")
    builder.adjust(2, 2)
    return builder.as_markup()


def duel_accept(pending_id: int) -> InlineKeyboardMarkup:
    """Кнопка принятия дуэли."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Принять бой", callback_data=f"duel:accept:{pending_id}")]
        ]
    )


def marriage_accept(pending_id: int) -> InlineKeyboardMarkup:
    """Кнопка согласия на брак."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💍 Согласиться", callback_data=f"marry:accept:{pending_id}")]
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
