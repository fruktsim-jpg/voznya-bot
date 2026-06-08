"""Инлайн-клавиатуры (кнопки) для ускорения взаимодействия.

Callback-данные имеют единый формат ``<feature>:<action>:<args...>`` и
проверяются в соответствующих обработчиках (защита от чужих нажатий,
повторов и просрочки).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def open_on_site(label: str, url: str) -> InlineKeyboardMarkup:
    """Одна URL-кнопка «открыть на сайте» (site-first, Release 2.2).

    Тяжёлые механики (кейсы, магазин, полный инвентарь, профиль, статистика)
    живут на сайте — бот лишь ведёт туда. URL-кнопка открывает страницу/Mini App
    во внешнем браузере или встроенном webview Telegram.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, url=url)]]
    )


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
    delivery_key: str,
    user_id: int,
    sell_amount: int,
    *,
    keep_label: str,
    sell_label: str,
    withdraw_label: str | None = None,
) -> InlineKeyboardMarkup:
    """Кнопки выбора после выпадения подарка из кейса (P1/P2/P7).

    «Оставить» — подарок остаётся в инвентаре (pending-доставка). «Продать» —
    мгновенная продажа за ешки (P5). «Вывести» — попытка авто-выдачи через
    Telegram (P2); при сбое подарок остаётся pending и появляется кнопка
    повтора. Callback несёт ключ доставки и id игрока: действовать может только
    владелец приза (проверка в хендлере), а операции защищены блокировкой
    строки доставки.
    """
    rows = [
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
    if withdraw_label is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text=withdraw_label,
                    callback_data=f"gift:withdraw:{delivery_key}:{user_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gift_retry(delivery_key: str, user_id: int, *, retry_label: str) -> InlineKeyboardMarkup:
    """Кнопка «Попробовать выдать ещё раз» после неудачной авто-выдачи (P6).

    Появляется, когда выдача не прошла по временной причине (нет Stars, ошибка
    Telegram API) и подарок остался pending. Повтор бьёт в ту же deliver_gift.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=retry_label,
                    callback_data=f"gift:withdraw:{delivery_key}:{user_id}",
                )
            ]
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
