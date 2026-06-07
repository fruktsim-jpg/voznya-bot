"""Топ-ап Stars для владельца + приём XTR-платежей. Минимальный флоу.

Это единственный способ получить Stars боту через Bot API (XTR-инвойс):
1. Админ шлёт `/topup <N>` → бот создаёт счёт в Stars (`currency='XTR'`,
   `provider_token=''`, цена = N звёзд).
2. Админ оплачивает счёт своими Stars → Telegram шлёт `pre_checkout_query`
   (отвечаем True) и затем `message.successful_payment` с
   `telegram_payment_charge_id`.
3. На `successful_payment` пишем приход в ``stars_ledger`` (идемпотентно по
   charge_id) и показываем новый баланс (`getMyStarBalance`).

Тот же приём платежа — фундамент будущего «донат Stars→ешки»: достаточно по
``payload`` различать topup/donation и при donation дополнительно начислять ешки
через экономику. Сейчас реализован ТОЛЬКО topup (минимально, без новых сущностей).

Полный путь Stars и P&L описаны в STARS_FLOW.md.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import (
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.filters import RuCommand
from app.core.logger import get_logger
from app.services import stars as stars_service
from app.services.telegram_gifts import get_star_balance

logger = get_logger(__name__)

router = Router(name="payments")

# Префикс payload инвойса: по нему различаем тип платежа (задел под donation).
PAYLOAD_TOPUP_PREFIX = "topup:"

TOPUP_USAGE = (
    "Пополнение баланса бота в Stars: <code>/topup N</code> "
    "(N — количество Stars, 1–10000).\n"
    "Откроется счёт — оплати его своими Stars."
)
TOPUP_ADMIN_ONLY = "Команда доступна только администратору бота."
TOPUP_BAD_AMOUNT = "Укажи количество Stars числом 1–10000, например: <code>/topup 50</code>."
TOPUP_INVOICE_TITLE = "Пополнение баланса бота"
TOPUP_INVOICE_DESC = "Зачисление {n} Telegram Stars на баланс бота «Возня»."
TOPUP_OK = "✅ Зачислено {n} ⭐. Баланс бота: {balance}."
TOPUP_OK_NO_BALANCE = "✅ Зачислено {n} ⭐ (баланс уточняется)."
TOPUP_DUPLICATE = "Этот платёж уже учтён."

MIN_STARS = 1
MAX_STARS = 10000


def _is_admin(message: Message) -> bool:
    return message.from_user is not None and get_settings().is_admin(
        message.from_user.id
    )


@router.message(RuCommand("topup", "topup"))
async def cmd_topup(message: Message, command_args: str) -> None:
    """Создаёт XTR-счёт на пополнение баланса бота (только админ)."""
    if not _is_admin(message):
        await message.answer(TOPUP_ADMIN_ONLY)
        return

    raw = (command_args or "").split()
    if not raw:
        await message.answer(TOPUP_USAGE)
        return
    try:
        amount = int(raw[0])
    except ValueError:
        await message.answer(TOPUP_BAD_AMOUNT)
        return
    if amount < MIN_STARS or amount > MAX_STARS:
        await message.answer(TOPUP_BAD_AMOUNT)
        return

    assert message.bot is not None
    # Для оплат в Stars: provider_token='', currency='XTR', amount в LabeledPrice
    # задаётся в звёздах (целое). Один LabeledPrice достаточно.
    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title=TOPUP_INVOICE_TITLE,
        description=TOPUP_INVOICE_DESC.format(n=amount),
        payload=f"{PAYLOAD_TOPUP_PREFIX}{amount}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} ⭐", amount=amount)],
    )


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Подтверждает пред-чек любого нашего Stars-счёта (товар цифровой, всегда ok).

    Если появятся другие типы инвойсов с ограничениями (например, лимитный
    донат), здесь можно отклонять по ``query.invoice_payload``.
    """
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, session: AsyncSession) -> None:
    """Фиксирует приход Stars в stars_ledger (идемпотентно по charge_id)."""
    sp = message.successful_payment
    if sp is None or message.from_user is None:
        return

    payload = sp.invoice_payload or ""
    # total_amount для XTR — это количество Stars (целое).
    amount_stars = int(sp.total_amount)
    charge_id = sp.telegram_payment_charge_id or ""

    # Тип платежа по payload (сейчас только topup; donation — будущий этап).
    reason = "topup" if payload.startswith(PAYLOAD_TOPUP_PREFIX) else "topup"

    assert message.bot is not None
    balance_after = await get_star_balance(message.bot)

    row = await stars_service.record_in(
        session,
        amount_stars=amount_stars,
        reason=reason,
        user_id=message.from_user.id,
        charge_id=charge_id,
        source="bot",
        balance_after=balance_after,
        meta={
            "payload": payload,
            "currency": sp.currency,
            "provider_charge_id": sp.provider_payment_charge_id,
        },
    )

    if row is None:
        await message.answer(TOPUP_DUPLICATE)
        return

    if balance_after is not None:
        await message.answer(TOPUP_OK.format(n=amount_stars, balance=balance_after))
    else:
        await message.answer(TOPUP_OK_NO_BALANCE.format(n=amount_stars))
