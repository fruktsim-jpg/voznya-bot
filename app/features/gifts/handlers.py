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
from app.core.keyboards import open_on_site, supports_web_app
from app.core.money import money

from app.core.responses import notify_and_cleanup
from app.features.gifts.service import (
    buy_gift,
    complete_gift_manually,
    deliver_gift,
    refund_gift,
)
from app.models import GiftCatalog
from app.repositories import gifts as gifts_repo
from app.services.telegram_gifts import get_star_balance, list_available_gifts

router = Router(name="gifts")


# --- Тексты (изолированы в фиче) --------------------------------------------
GIFTS_HEADER = "🛒 <b>Магазин</b>\nКопи ешки и забирай реальные Telegram Gifts и Premium."

GIFTS_EMPTY = "🎁 Подарков пока нет в наличии. Загляни позже."
GIFTS_ROW = "<b>{name}</b> — {price}{stock}"
GIFTS_ROW_STOCK = " · осталось {n}"
GIFT_BUY_BTN = "Купить «{name}» за {price}"

# Site-first (Release 2.2): магазин живёт на сайте, бот лишь ведёт туда.
SHOP_SITE_CARD = (
    "🛍 <b>Магазин Возни</b>\n\n"
    "Telegram Gifts, Premium и будущие товары — на сайте. Там удобно выбирать, "
    "покупать и сразу управлять покупкой в инвентаре."
)
SHOP_SITE_BTN = "🛍 Открыть магазин"


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
    "send_gift_unsupported": (
        "sendGift недоступен в этой версии aiogram (нужен 3.14+); "
        "обнови зависимость или выдай вручную"
    ),
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


@router.message(RuCommand("магазин", "shop", "подарки", "gifts"))
async def cmd_gifts(message: Message, session: AsyncSession) -> None:
    """Site-first (Release 2.2): магазин на сайте — бот ведёт туда карточкой.

    Полноценный магазин (выбор, покупка, управление покупкой) живёт на сайте.
    Бот больше не обслуживает витрину внутри Telegram, чтобы тяжёлая механика не
    развивалась в двух местах. Команды-алиасы (/магазин, /shop, /подарки,
    /gifts) показывают карточку с кнопкой на /gifts сайта.
    """
    if message.from_user is None:
        return
    url = f"{get_settings().website_url}/gifts"
    await message.answer(
        SHOP_SITE_CARD,
        reply_markup=open_on_site(
            SHOP_SITE_BTN,
            url,
            prefer_web_app=supports_web_app(message.chat.type),
        ),
    )



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
    """Site-first (Release 2.2): покупка подарков перенесена на сайт.

    Кнопка `gift:buy` больше не вешается на новые сообщения, но старые сообщения
    в чате могут её ещё содержать. Чтобы НИГДЕ не осталось сценария покупки
    внутри Telegram, при нажатии ведём игрока в магазин на сайте, а не списываем
    ешки. Конвейер `buy_gift`/`deliver_gift` остаётся только для сайта и админа.
    """
    await callback.answer(
        "Магазин теперь на сайте. Открой актуальную витрину через /магазин.",
        show_alert=True,
    )



# --- Игрок: статус своих покупок --------------------------------------------
MY_GIFTS_EMPTY = "🎁 Ты ещё ничего не покупал в магазине подарков."
MY_GIFTS_HEADER = "🎁 <b>Твои подарки</b>:"
# Человекочитаемый статус доставки для игрока.
MY_GIFTS_STATUS = {
    "completed": "✅ отправлен",
    "pending": "⏳ оплачен, в очереди на выдачу",
    "cancelled": "↩️ отменён, ешки вернули",
}
MY_GIFTS_ROW = "• «{item}» — {status}"


@router.message(RuCommand("моиподарки", "mygifts"))
async def cmd_my_gifts(message: Message, session: AsyncSession) -> None:
    """Показывает игроку его покупки и их статус выдачи.

    Закрывает цикл: после покупки игрок может в любой момент перепроверить,
    отправлен ли подарок, ждёт ли выдачи или был возвращён.
    """
    if message.from_user is None:
        return
    deliveries = await gifts_repo.get_recent_deliveries(
        session, user_id=message.from_user.id, limit=20
    )
    if not deliveries:
        await notify_and_cleanup(session, message, MY_GIFTS_EMPTY)
        return

    # Имена одним запросом — игроку показываем название подарка, не код.
    names = await gifts_repo.get_names_by_codes(
        session, [d.item_code or "" for d in deliveries]
    )
    lines = [MY_GIFTS_HEADER]
    for d in deliveries:
        status = MY_GIFTS_STATUS.get(d.status, d.status)
        item_label = names.get(d.item_code or "") or "подарок"
        lines.append(MY_GIFTS_ROW.format(item=item_label, status=status))
    url = f"{get_settings().website_url}/inventory"
    await message.answer(
        "\n".join(lines),
        reply_markup=open_on_site(
            "🎒 Управлять подарками",
            url,
            prefer_web_app=supports_web_app(message.chat.type),
        ),
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


# --- Админ: ручное управление выдачей подарков ------------------------------
# Сценарий: автодоставка через Telegram не сработала (выключена, нет gift_id,
# мало Stars). Покупка уже оплачена (ешки списаны, есть pending-доставка). Админ
# отправляет подарок вручную и закрывает доставку, либо отменяет с возвратом.
PENDING_EMPTY = "🎁 Нет подарков в ожидании выдачи (pending)."
PENDING_HEADER = "🎁 <b>Подарки в ожидании выдачи</b> ({n}):"
PENDING_ROW = (
    "• <code>{key}</code>\n"
    "  {origin} «{item}» → пользователю <code>{user}</code>{stars}"
)
# Откуда взялся подарок (по meta.source): приз кейса или покупка магазина.
PENDING_ORIGIN_CASE = "🎰"
PENDING_ORIGIN_SHOP = "🛒"

PENDING_HINT = (
    "\n\nВыдать вручную: <code>/gifts_done &lt;ключ&gt;</code>\n"
    "Отменить с возвратом: <code>/gifts_refund &lt;ключ&gt;</code>"
)

GIFT_KEY_USAGE = (
    "Укажи ключ доставки (idempotency_key) из <code>/gifts_pending</code>:\n"
    "<code>/{cmd} &lt;ключ&gt;</code>"
)
GIFT_DELIVERY_NOT_FOUND = "🤷 Доставки с таким ключом нет."
GIFT_DELIVERY_NOT_PENDING = "⚠️ Эта доставка уже обработана (не pending)."
GIFT_DONE_OK = "✅ Подарок отмечен как выданный вручную. Статистика обновлена."
GIFT_REFUND_OK = "↩️ Доставка отменена, ешки возвращены игроку."
GIFT_RETRY_OK = "✅ Авто-выдача удалась — подарок отправлен."
GIFT_RETRY_PENDING = "⏳ Снова временная ошибка ({error}). Оставил pending — попробуй позже или /gifts_done."
GIFT_RETRY_REFUNDED = "↩️ Постоянная ошибка — доставка отменена, ешки возвращены."



def _pending_admin_keyboard(key: str) -> InlineKeyboardMarkup:
    """Кнопки админ-действий под одной pending-доставкой (P6).

    callback_data: ``gd:<action>:<key>`` (action: done|retry|refund). Ключ —
    ``giftbuy:<uid>:<16hex>`` (~35 симв.), с префиксом укладывается в лимит 64Б.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔁 Повторить", callback_data=f"gd:retry:{key}"),
                InlineKeyboardButton(text="✅ Выдать", callback_data=f"gd:done:{key}"),
                InlineKeyboardButton(text="↩️ Возврат", callback_data=f"gd:refund:{key}"),
            ]
        ]
    )


@router.message(RuCommand("gifts_pending", "gifts_pending"))
async def cmd_gifts_pending(message: Message, session: AsyncSession) -> None:
    """Показывает оплаченные, но ещё не выданные подарки (только админ).

    Каждая заявка отправляется отдельным сообщением со своими кнопками:
    Повторить авто-выдачу / Выдать вручную / Возврат — чтобы админ завершал
    цикл прямо в чате, без копирования ключей.
    """
    if not _admin_ok(message):
        await message.answer(ADMIN_ONLY)
        return

    pending = await gifts_repo.get_pending_deliveries(session, limit=100)
    if not pending:
        await message.answer(PENDING_EMPTY)
        return

    await message.answer(PENDING_HEADER.format(n=len(pending)))
    # Имена подарков одним запросом — админ видит человекочитаемое название, а не
    # внутренний код (релизное требование). Код оставляем мелким техническим
    # суффиксом, т.к. он нужен для ручных команд.
    names = await gifts_repo.get_names_by_codes(
        session, [d.item_code or "" for d in pending]
    )
    for d in pending:
        star_cost = int((d.meta or {}).get("star_cost") or 0)
        stars = f" · {star_cost} ⭐" if star_cost else ""
        # Причина задержки (если воркер/выдача её записали) — сразу видно админу.
        reason_raw = (d.meta or {}).get("delivery_error")
        reason = (
            f"\n  ⚠️ причина: {DELIVERY_REASONS.get(reason_raw, reason_raw)}"
            if reason_raw
            else ""
        )
        origin = (
            PENDING_ORIGIN_CASE
            if (d.meta or {}).get("source") == "case"
            else PENDING_ORIGIN_SHOP
        )
        item_label = names.get(d.item_code or "") or (d.item_code or "?")
        row = PENDING_ROW.format(
            key=d.idempotency_key,
            origin=origin,
            item=item_label,
            user=d.recipient_user_id,
            stars=stars,
        )

        await message.answer(
            row + reason,
            reply_markup=_pending_admin_keyboard(d.idempotency_key or ""),
        )




@router.message(RuCommand("gifts_done", "gifts_done"))
async def cmd_gifts_done(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Отмечает pending-доставку как выданную вручную (только админ).

    Деньги игрока не трогаем (покупка уже зафиксирована), но доставка переходит
    в ``completed`` и единица реализуется (reserved-1, sold_count+1) — чтобы
    подарок считался отправленным и попадал в аналитику.
    """
    if not _admin_ok(message):
        await message.answer(ADMIN_ONLY)
        return
    key = (command_args or "").strip()
    if not key:
        await message.answer(GIFT_KEY_USAGE.format(cmd="gifts_done"))
        return

    outcome = await complete_gift_manually(
        session,
        idempotency_key=key,
        admin_user_id=message.from_user.id,
        channel="bot",
    )
    if outcome.status == "completed":
        await message.answer(GIFT_DONE_OK)
    elif outcome.error == "delivery_not_found":
        await message.answer(GIFT_DELIVERY_NOT_FOUND)
    else:
        await message.answer(GIFT_DELIVERY_NOT_PENDING)


@router.message(RuCommand("gifts_retry", "gifts_retry"))
async def cmd_gifts_retry(
    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Повторяет АВТО-выдачу pending-доставки (только админ) — P6.

    Тот же конвейер ``deliver_gift``, что и при покупке/выводе. Полезно, когда
    ошибка была временной (мало Stars, сбой Telegram API): после устранения
    причины админ жмёт retry и подарок уходит без ручной отправки. Постоянная
    ошибка приведёт к отмене с возвратом (логика внутри deliver_gift).
    """
    if not _admin_ok(message):
        await message.answer(ADMIN_ONLY)
        return
    key = (command_args or "").strip()
    if not key:
        await message.answer(GIFT_KEY_USAGE.format(cmd="gifts_retry"))
        return

    settings = get_settings()
    outcome = await deliver_gift(
        session,
        message.bot,
        idempotency_key=key,
        enabled=settings.gifts_delivery_enabled,
        channel="bot",
    )
    if outcome.status == "completed":
        await message.answer(GIFT_RETRY_OK)
    elif outcome.status == "cancelled":
        await message.answer(GIFT_RETRY_REFUNDED)
    elif outcome.status == "skip" and outcome.error == "delivery_not_found":
        await message.answer(GIFT_DELIVERY_NOT_FOUND)
    else:
        reason = DELIVERY_REASONS.get(outcome.error or "", outcome.error or "неизвестно")
        await message.answer(GIFT_RETRY_PENDING.format(error=reason))


@router.message(RuCommand("gifts_refund", "gifts_refund"))
async def cmd_gifts_refund(

    message: Message, session: AsyncSession, command_args: str
) -> None:
    """Отменяет pending-доставку с возвратом ешек игроку (только админ)."""
    if not _admin_ok(message):
        await message.answer(ADMIN_ONLY)
        return
    key = (command_args or "").strip()
    if not key:
        await message.answer(GIFT_KEY_USAGE.format(cmd="gifts_refund"))
        return

    outcome = await refund_gift(
        session, idempotency_key=key, channel="bot", reason="admin_manual"
    )
    if outcome.status == "cancelled":
        await message.answer(GIFT_REFUND_OK)
    elif outcome.error == "delivery_not_found":
        await message.answer(GIFT_DELIVERY_NOT_FOUND)
    else:
        await message.answer(GIFT_DELIVERY_NOT_PENDING)


@router.callback_query(F.data.startswith("gd:"))
async def cb_gift_delivery_admin(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Админ-кнопки под pending-доставкой: Повторить / Выдать / Возврат (P6).

    callback_data = ``gd:<action>:<key>``. Только админ. Выполняет то же, что
    одноимённые команды, и отвечает всплывашкой + текстом, чтобы кнопки в
    списке `/gifts_pending` закрывали цикл без копирования ключей.
    """
    if callback.from_user is None or not get_settings().is_admin(callback.from_user.id):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return
    # gd:<action>:<key>  (key может содержать ':' — берём остаток как ключ)
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    action, key = parts[1], parts[2]
    if not key:
        await callback.answer()
        return

    if action == "retry":
        outcome = await deliver_gift(
            session,
            callback.bot,
            idempotency_key=key,
            enabled=get_settings().gifts_delivery_enabled,
            channel="bot",
        )
        if outcome.status == "completed":
            text = GIFT_RETRY_OK
        elif outcome.status == "cancelled":
            text = GIFT_RETRY_REFUNDED
        elif outcome.status == "skip" and outcome.error == "delivery_not_found":
            text = GIFT_DELIVERY_NOT_FOUND
        else:
            reason = DELIVERY_REASONS.get(outcome.error or "", outcome.error or "неизвестно")
            text = GIFT_RETRY_PENDING.format(error=reason)
    elif action == "done":
        outcome = await complete_gift_manually(
            session,
            idempotency_key=key,
            admin_user_id=callback.from_user.id,
            channel="bot",
        )
        if outcome.status == "completed":
            text = GIFT_DONE_OK
        elif outcome.error == "delivery_not_found":
            text = GIFT_DELIVERY_NOT_FOUND
        else:
            text = GIFT_DELIVERY_NOT_PENDING
    elif action == "refund":
        outcome = await refund_gift(
            session, idempotency_key=key, channel="bot", reason="admin_manual"
        )
        if outcome.status == "cancelled":
            text = GIFT_REFUND_OK
        elif outcome.error == "delivery_not_found":
            text = GIFT_DELIVERY_NOT_FOUND
        else:
            text = GIFT_DELIVERY_NOT_PENDING
    else:
        await callback.answer()
        return

    await callback.answer()
    # Убираем кнопки у обработанной заявки (best-effort) и пишем результат.
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)

        except Exception:  # noqa: BLE001
            pass
        await callback.message.answer(text)




