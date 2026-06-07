"""Хендлеры магазина Gifts: /подарки (витрина) и покупка по кнопке.

Покупка идёт ТОЛЬКО через :func:`app.features.gifts.service.buy_gift` — единую
атомарную точку. После успешной покупки покупка фиксируется (commit), затем
выполняется попытка выдачи :func:`deliver_gift` (внешний вызов Telegram вне
денежной транзакции). Тексты — здесь же (фича изолирована).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.money import money
from app.core.responses import notify_and_cleanup
from app.features.gifts.service import buy_gift, deliver_gift
from app.models import GiftCatalog
from app.repositories import gifts as gifts_repo
from app.services.telegram_gifts import get_star_balance, list_available_gifts

router = Router(name="gifts")


# --- Тексты (изолированы в фиче) --------------------------------------------
GIFTS_HEADER = "🎁 <b>Магазин подарков</b>\nКопи ешки и забирай реальные Telegram Gifts."
GIFTS_EMPTY = "🎁 Подарков пока нет в наличии. Загляни позже."
GIFTS_ROW = "<b>{name}</b> — {price}{stock}"
GIFTS_ROW_STOCK = " · осталось {n}"
GIFT_BUY_BTN = "Купить «{name}» за {price}"

BUY_NOT_FOUND = "Такого подарка нет."
BUY_INACTIVE = "Подарок «{name}» сейчас недоступен."
BUY_SOLD_OUT = "Подарок «{name}» раскуплен."
BUY_NOT_ENOUGH = "Не хватает ешек на «{name}»: нужно {price}."
BUY_OK = "🎁 Куплен «{name}» за {price}. Баланс: {balance}.\n{delivery}"
BUY_ERROR = "Не получилось купить подарок. Попробуй позже."

DELIVERY_SENT = "✅ Подарок отправлен!"
DELIVERY_PENDING = "⏳ Подарок оплачен, отправлю чуть позже."
DELIVERY_PENDING_ADMIN = "⏳ Оплачено. Выдача отложена (pending). Причина: {reason}."
DELIVERY_REFUNDED = "⚠️ Не удалось отправить подарок — ешки возвращены."

# Человекочитаемые причины задержки выдачи (для админа).
DELIVERY_REASONS = {
    "delivery_disabled": "выдача выключена (GIFTS_DELIVERY_ENABLED=false)",
    "no_telegram_gift_id": "у позиции каталога не задан telegram_gift_id",
    "insufficient_bot_stars": "не хватает Stars на балансе бота",
}


NOT_YOURS = "Это не твоя кнопка."


def _shop_keyboard(gifts) -> InlineKeyboardMarkup:
    """Кнопки покупки под витриной (по одной на позицию)."""
    rows = [
        [
            InlineKeyboardButton(
                text=GIFT_BUY_BTN.format(name=g.name, price=money(g.price_eshki)),
                callback_data=f"gift:buy:{g.code}",
            )
        ]
        for g in gifts
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(RuCommand("подарки", "gifts"))
async def cmd_gifts(message: Message, session: AsyncSession) -> None:
    """Витрина подарков."""
    if message.from_user is None:
        return
    gifts = await gifts_repo.get_active_gifts(session)
    if not gifts:
        await notify_and_cleanup(session, message, GIFTS_EMPTY)
        return

    lines = [GIFTS_HEADER]
    for g in gifts:
        stock = ""
        if g.stock is not None:
            left = g.stock - g.reserved - g.sold_count
            stock = GIFTS_ROW_STOCK.format(n=max(0, left))
        lines.append(GIFTS_ROW.format(name=g.name, price=money(g.price_eshki), stock=stock))
    await message.answer("\n".join(lines), reply_markup=_shop_keyboard(gifts))


def _render_buy_failure(result) -> str | None:
    """Текст для неуспешной покупки (или None при успехе)."""
    if result.status == "not_found":
        return BUY_NOT_FOUND
    if result.status == "inactive":
        return BUY_INACTIVE.format(name=result.gift_name)
    if result.status == "sold_out":
        return BUY_SOLD_OUT.format(name=result.gift_name)
    if result.status == "not_enough":
        return BUY_NOT_ENOUGH.format(name=result.gift_name, price=money(result.price))
    if result.status != "ok":
        return BUY_ERROR
    return None


@router.callback_query(F.data.startswith("gift:buy:"))
async def cb_gift_buy(callback: CallbackQuery, session: AsyncSession) -> None:
    """Покупка подарка по кнопке: списать ешки, затем попытаться выдать."""
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    # gift:buy:<code>
    if len(parts) != 3:
        await callback.answer()
        return
    code = parts[2]
    user_id = callback.from_user.id

    result = await buy_gift(session, user_id=user_id, code=code, channel="bot")
    failure = _render_buy_failure(result)
    if failure is not None:
        await callback.answer()
        await callback.message.answer(failure)
        return

    # Фиксируем покупку (списание + резерв + pending-доставка) ДО внешней выдачи.
    await session.commit()

    # Попытка выдачи (внешний вызов Telegram). enabled из настроек: пока выдача
    # не подключена — доставка останется pending (ешки не теряются).
    settings = get_settings()
    outcome = await deliver_gift(
        session,
        callback.bot,
        idempotency_key=result.idempotency_key or "",
        enabled=settings.gifts_delivery_enabled,
        channel="bot",
    )
    # Результат выдачи коммитит middleware при возврате из хендлера.

    if outcome.status == "completed":
        delivery_line = DELIVERY_SENT
    elif outcome.status == "cancelled":
        delivery_line = DELIVERY_REFUNDED
        if get_settings().is_admin(user_id) and outcome.error:
            delivery_line += f"\n(ошибка: {outcome.error})"
    elif get_settings().is_admin(user_id):

        # Админу показываем точную причину задержки (не гадаем).
        reason = DELIVERY_REASONS.get(outcome.error or "", outcome.error or "неизвестно")
        delivery_line = DELIVERY_PENDING_ADMIN.format(reason=reason)
    else:
        delivery_line = DELIVERY_PENDING

    await callback.answer()

    await callback.message.answer(
        BUY_OK.format(
            name=result.gift_name,
            price=money(result.price),
            balance=money(result.balance or 0),
            delivery=delivery_line,
        )
    )


# --- Админ: подключение реальных Telegram gift_id ---------------------------
ADMIN_ONLY = "Команда доступна только администратору бота."
AVAIL_EMPTY = (
    "Telegram не вернул доступных подарков (getAvailableGifts пуст или метод "
    "недоступен в этой версии aiogram). Баланс Stars бота: {balance}."
)
AVAIL_HEADER = "🎁 <b>Доступные у Telegram подарки</b> (баланс бота: {balance} ⭐):"
AVAIL_ROW = "<code>{id}</code> — {stars} ⭐{remaining}"
SETID_USAGE = (
    "Привязать реальный gift_id к позиции каталога:\n"
    "<code>/gifts_setid &lt;code&gt; &lt;telegram_gift_id&gt;</code>\n"
    "Список id — командой <code>/gifts_available</code>."
)
SETID_NOT_FOUND = "Позиции каталога с кодом «{code}» нет."
SETID_OK = "✅ «{code}» ← telegram_gift_id <code>{gid}</code>. Можно выдавать."


def _admin_ok(message: Message) -> bool:
    return message.from_user is not None and get_settings().is_admin(
        message.from_user.id
    )


@router.message(RuCommand("gifts_available", "gifts_available"))
async def cmd_gifts_available(message: Message) -> None:
    """Показывает реальные подарки и их id из Telegram (getAvailableGifts)."""
    if not _admin_ok(message):
        await message.answer(ADMIN_ONLY)
        return
    assert message.bot is not None
    balance = await get_star_balance(message.bot)
    balance_str = "—" if balance is None else str(balance)
    gifts = await list_available_gifts(message.bot)
    if not gifts:
        await message.answer(AVAIL_EMPTY.format(balance=balance_str))
        return
    lines = [AVAIL_HEADER.format(balance=balance_str)]
    for g in gifts:
        rem = "" if g["remaining"] is None else f" · осталось {g['remaining']}"
        lines.append(AVAIL_ROW.format(id=g["id"], stars=g["star_count"], remaining=rem))
    await message.answer("\n".join(lines))


@router.message(RuCommand("gifts_setid", "gifts_setid"))
async def cmd_gifts_setid(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Привязывает реальный telegram_gift_id к позиции каталога (только админ)."""
    if not _admin_ok(message):
        await message.answer(ADMIN_ONLY)
        return
    parts = (command_args or "").split()
    if len(parts) < 2:
        await message.answer(SETID_USAGE)
        return
    code, gid = parts[0], parts[1]

    gift = await gifts_repo.get_gift_by_code(session, code)
    if gift is None:
        await message.answer(SETID_NOT_FOUND.format(code=code))
        return

    await session.execute(
        update(GiftCatalog)
        .where(GiftCatalog.code == code)
        .values(telegram_gift_id=gid)
    )
    await message.answer(SETID_OK.format(code=code, gid=gid))


