"""Инлайн-клавиатуры (кнопки) для ускорения взаимодействия.

Callback-данные имеют единый формат ``<feature>:<action>:<args...>`` и
проверяются в соответствующих обработчиках (защита от чужих нажатий,
повторов и просрочки).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup



def treasure_claim() -> InlineKeyboardMarkup:
    """Кнопка «Забрать клад» для сообщения о появлении клада.

    Кнопку видит весь чат; клад достаётся тому, кто нажмёт первым —
    логика гонки уже обеспечена блокировкой строки в claim_treasure().
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Забрать клад", callback_data="treasure:claim")]
        ]
    )


def duel_accept(pending_id: int, *, with_decline: bool = True) -> InlineKeyboardMarkup:
    """Кнопки дуэли.

    Для персонального вызова (``with_decline=True``) показываем «Принять» и
    «Слиться»: вызванному есть от чего отказываться. Для открытого вызова
    (``with_decline=False``) кнопки отказа нет — приглашение адресовано всему
    чату, отказываться некому.
    """
    row = [
        InlineKeyboardButton(
            text="⚔️ Принять бой", callback_data=f"duel:accept:{pending_id}"
        )
    ]
    if with_decline:
        row.append(
            InlineKeyboardButton(
                text="🏳️ Слиться", callback_data=f"duel:decline:{pending_id}"
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[row])




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


def case_open(case_item_code: str, user_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Открыть» для карточки кейса.

    Callback несёт код кейса и id игрока: открыть может только адресат (проверка
    в хендлере), а сама выдача защищена блокировками строк в open_case().
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎁 Открыть",
                    callback_data=f"case:open:{case_item_code}:{user_id}",
                )
            ]
        ]
    )


def case_gift_choice(
    delivery_key: str, user_id: int, sell_amount: int, *, keep_label: str, sell_label: str
) -> InlineKeyboardMarkup:
    """Кнопки выбора после выпадения подарка из кейса (P1/P7).

    «Оставить» — подарок остаётся pending-доставкой (выдаст админ). «Продать» —
    мгновенная продажа за ешки (P5). Callback несёт ключ доставки и id игрока:
    действовать может только владелец приза (проверка в хендлере), а сама
    продажа защищена блокировкой строки доставки.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=keep_label,
                    callback_data=f"gift:keep:{delivery_key}:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=sell_label.format(amount=sell_amount),
                    callback_data=f"gift:sell:{delivery_key}:{user_id}",
                )
            ],
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
