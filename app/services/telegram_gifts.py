"""Адаптер отправки реальных Telegram Gifts — ЕДИНСТВЕННАЯ точка внешнего вызова.

Изолирует Bot API (`sendGift`, `getMyStarBalance`, `getAvailableGifts`) от
бизнес-логики магазина. Подтверждённые факты API (см. TELEGRAM_GIFTS_AUDIT.md):

* ``sendGift(user_id, gift_id)`` возвращает только ``True`` — НЕТ charge_id;
* ``getMyStarBalance`` возвращает баланс Stars бота (нужен перед отправкой);
* боту нужны Stars на балансе — без них отправка не пройдёт.

Режим работы управляется настройкой ``gifts_delivery_enabled``:
* False (по умолчанию, V1 без подключённой выдачи) — STUB: ничего не шлёт,
  возвращает «не настроено»; доставка остаётся ``pending`` (деньги уже списаны,
  ешки вернёт refund по решению админа/логики). Это безопасно: реальные Stars не
  тратятся, пока выдача явно не включена.
* True — реальный вызов Bot API.

Функции НЕ трогают БД — только внешний вызов и нормализованный результат.
Решение о статусе/возврате принимает вызывающий сервис (gifts.service).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aiogram import Bot

from app.core.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    """Нормализованный итог попытки выдачи подарка.

    :param ok: подарок реально отправлен (Bot API вернул True);
    :param retriable: при ok=False — можно повторить позже (временная ошибка);
    :param error: машинно/человекочитаемая причина (для meta.error);
    :param star_balance_before/after: баланс Stars бота вокруг вызова (косвенное
        доказательство расхода — charge_id Telegram не возвращает);
    :param extra: прочие данные для meta (api_ok и т.п.).
    """

    ok: bool
    retriable: bool = False
    error: str | None = None
    star_balance_before: int | None = None
    star_balance_after: int | None = None
    extra: dict = field(default_factory=dict)


async def get_star_balance(bot: Bot) -> int | None:
    """Возвращает баланс Stars бота через getMyStarBalance (или None при ошибке).

    Метод доступен не во всех версиях aiogram (Bot API 9.0+). Если его нет —
    возвращаем None (баланс неизвестен), это не блокирует выдачу.
    """
    method = getattr(bot, "get_my_star_balance", None)
    if method is None:
        return None
    try:
        amount = await method()
        # StarAmount.amount — целое число Stars.
        return int(getattr(amount, "amount", 0))
    except Exception as exc:  # noqa: BLE001
        logger.warning("getMyStarBalance failed: %s", exc)
        return None



async def send_gift(
    bot: Bot,
    *,
    user_id: int,
    telegram_gift_id: str,
    star_cost: int,
    enabled: bool,
    text: str | None = None,
) -> DeliveryResult:
    """Пытается отправить реальный Telegram Gift игроку ``user_id``.

    Безопасный контракт:
    * если выдача не включена (``enabled=False``) или нет ``telegram_gift_id`` —
      возвращает retriable-неуспех (доставка остаётся pending, Stars не тратятся);
    * перед отправкой проверяет баланс Stars бота (``getMyStarBalance``);
    * сам вызов ``sendGift`` ловит ошибки и классифицирует на retriable/постоянные.
    """
    if not enabled:
        return DeliveryResult(
            ok=False, retriable=True, error="delivery_disabled"
        )
    if not telegram_gift_id:
        return DeliveryResult(
            ok=False, retriable=True, error="no_telegram_gift_id"
        )

    balance_before = await get_star_balance(bot)
    if balance_before is not None and balance_before < star_cost:
        # Боту не хватает Stars — НЕ пытаемся слать (иначе гарантированная ошибка).
        return DeliveryResult(
            ok=False,
            retriable=True,
            error="insufficient_bot_stars",
            star_balance_before=balance_before,
        )

    try:
        result = await bot.send_gift(
            user_id=user_id, gift_id=telegram_gift_id, text=text
        )
    except Exception as exc:  # noqa: BLE001
        # Классификация: сетевые/временные — retriable; прочее — постоянное.
        msg = str(exc)
        retriable = _is_retriable_error(msg)
        logger.warning(
            "sendGift failed (user=%s gift=%s retriable=%s): %s",
            user_id, telegram_gift_id, retriable, msg,
        )
        return DeliveryResult(
            ok=False,
            retriable=retriable,
            error=msg[:480],
            star_balance_before=balance_before,
        )

    if result is not True:
        # Неоднозначный ответ — НЕ считаем успехом, в ручной разбор.
        return DeliveryResult(
            ok=False,
            retriable=False,
            error="ambiguous_response",
            star_balance_before=balance_before,
            extra={"raw": str(result)[:200]},
        )

    balance_after = await get_star_balance(bot)
    return DeliveryResult(
        ok=True,
        star_balance_before=balance_before,
        star_balance_after=balance_after,
        extra={"api_ok": True},
    )


# Подстроки ошибок Telegram, при которых имеет смысл ретрай (временные).
_RETRIABLE_HINTS = (
    "timeout",
    "timed out",
    "too many requests",
    "retry after",
    "bad gateway",
    "service unavailable",
    "internal server error",
    "connection",
    "network",
)


def _is_retriable_error(message: str) -> bool:
    """Грубая классификация ошибки Bot API на временную (ретраить) и постоянную."""
    low = message.lower()
    return any(h in low for h in _RETRIABLE_HINTS)
