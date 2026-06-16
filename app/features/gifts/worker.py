"""Авто-выдача подарков, выведенных с сайта/мини-аппа (P2/P6).

Зачем нужен: сайт (Vercel) и бот (VPS) не могут вызывать друг друга, но делят
одну Postgres. Когда игрок жмёт «Вывести» на сайте, действие лишь помечает
доставку ``meta.withdraw_requested = true`` (см. lib/inventory-actions.withdraw)
— реальную отправку через Telegram умеет ТОЛЬКО бот (у него токен и Stars).

Этот фоновой воркер (APScheduler) периодически забирает такие помеченные
pending-доставки и пытается выдать их тем же конвейером :func:`deliver_gift`,
что и кнопка «Вывести» в самом боте:

  * успех      → completed, игроку приходит уведомление «подарок отправлен»;
  * temp-fail  → остаётся pending (попробуем в следующий тик; для игрока это
                 «в очереди»), копятся attempts/last_error в meta;
  * perm-fail  → доставка отменена с возвратом стоимости (логика deliver_gift).

Чтобы воркер не молотил одну и ту же вечно-падающую доставку, после исчерпания
лимита попыток (MAX_ATTEMPTS) снимаем флаг ``withdraw_requested`` — подарок
остаётся в инвентаре, и им займётся админ (/gifts_pending). Идемпотентность и
блокировки — внутри deliver_gift (FOR UPDATE), повторные тики безопасны.
"""

from __future__ import annotations

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.core.logger import get_logger
from app.features.gifts.service import deliver_gift
from app.repositories import gifts as gifts_repo
from app.repositories import users as users_repo
from app.settings import texts


logger = get_logger(__name__)

# Как часто опрашиваем очередь вывода (сек). Достаточно быстро для «почти сразу»,
# но без лишней нагрузки на БД/Telegram.
WITHDRAW_POLL_SECONDS = 30
# Сколько доставок обрабатываем за один тик (защита от долгих циклов).
WITHDRAW_BATCH = 20
# После стольких неудачных авто-попыток перестаём дёргать доставку сами —
# снимаем флаг вывода, оставляем админу (/gifts_pending).
MAX_ATTEMPTS = 5


async def process_withdraw_queue(
    bot: Bot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Один проход по очереди вывода: пытается выдать помеченные доставки.

    Каждую доставку обрабатываем в отдельной транзакции (свой ``session``),
    чтобы сбой одной не откатывал остальные. Внешний вызов Telegram изолирован
    внутри deliver_gift.
    """
    settings = get_settings()
    async with sessionmaker() as session:
        pending = await gifts_repo.get_withdraw_requested(session, limit=WITHDRAW_BATCH)
        # Имена подарков резолвим одним батч-запросом (без N+1 по каталогу):
        # пользователю НИКОГДА не показываем внутренний код (gift_bear) — только имя.
        names_by_code = await gifts_repo.get_names_by_codes(
            session, [d.item_code for d in pending if d.item_code]
        )
        keys = [
            (
                d.idempotency_key,
                d.recipient_user_id,
                # Фолбэк «подарок», если код пуст или имя не нашлось в каталоге.
                names_by_code.get(d.item_code, "подарок") if d.item_code else "подарок",
                # «Подарить другу» через очередь (бот был недоступен с сайта):
                # meta.gift_to = @username|id получателя реального подарка.
                (d.meta or {}).get("gift_to"),
            )
            for d in pending
        ]


    for idem, user_id, gift_name, gift_to in keys:
        async with sessionmaker() as session:
            try:
                # Резолвим получателя «Подарить другу» (если задан). Не нашли —
                # снимаем флаг очереди, чтобы не зацикливаться, и идём дальше.
                recipient_override = await _resolve_gift_to(session, gift_to)
                if gift_to and recipient_override is None:
                    await _drop_unresolved_gift_to(session, idem)
                    await session.commit()
                    await _notify(bot, user_id, texts.GIFT_FRIEND_NOT_FOUND.format(gift=gift_name))
                    continue

                outcome = await deliver_gift(
                    session,
                    bot,
                    idempotency_key=idem,
                    enabled=settings.gifts_delivery_enabled,
                    channel="site",
                    recipient_override=recipient_override,
                )
            except Exception:  # noqa: BLE001 — одна доставка не должна валить воркер
                logger.exception("auto-withdraw failed for %s", idem)
                await session.rollback()
                continue


            if outcome.status == "completed":
                await session.commit()
                logger.info("auto-withdraw delivered %s to %s", idem, user_id)
                await _notify(bot, user_id, texts.GIFT_WITHDRAW_SENT.format(gift=gift_name))
            elif outcome.status == "pending":
                # Временная ошибка: оставляем в очереди. Если попыток слишком
                # много — снимаем флаг (отдадим админу), чтобы не зациклиться.
                await _maybe_give_up(session, idem, user_id, gift_name, bot)
                await session.commit()
            else:
                # cancelled (возврат сделан внутри deliver_gift) или skip
                # (уже обработана) — просто фиксируем.
                await session.commit()


async def _display_name(session: AsyncSession, item_code: str | None) -> str:
    """Человекочитаемое имя подарка по коду каталога (фолбэк — «подарок»).

    Релизное требование: пользователь НИКОГДА не видит внутренний код
    (``gift_bear``/``premium_3m``). Имя берём из ``gift_catalog`` (там же лежат
    Premium-позиции). Если код пуст или не нашёлся — нейтральное «подарок».
    """
    if not item_code:
        return "подарок"
    gift = await gifts_repo.get_gift_by_code(session, item_code)
    if gift is not None and gift.name:
        return gift.name
    return "подарок"


async def _resolve_gift_to(
    session: AsyncSession, gift_to: str | None
) -> int | None:

    """@username|id из meta.gift_to → user_id получателя (или None).

    Числовой id берём как есть; username ищем по таблице users (получатель
    должен быть в Возне — sendGift умеет только по user_id). None, если не
    задан или не нашли.
    """
    if not gift_to:
        return None
    raw = str(gift_to).strip()
    if raw.lstrip("@").isdigit():
        return int(raw.lstrip("@"))
    target = await users_repo.get_user_by_username(session, raw)
    return target.user_id if target else None


async def _drop_unresolved_gift_to(session: AsyncSession, idem: str) -> None:
    """Снимает флаг очереди, если получателя «Подарить другу» не нашли.

    Предмет остаётся pending в инвентаре отправителя (он сам решит судьбу), но
    больше не дёргается воркером впустую.
    """
    delivery = await gifts_repo.get_delivery_for_update(session, idem)
    if delivery is None or delivery.status != "pending":
        return
    meta = dict(delivery.meta or {})
    meta["withdraw_requested"] = False
    meta["gift_to_unresolved"] = True
    delivery.meta = meta


async def _maybe_give_up(

    session: AsyncSession,
    idem: str,
    user_id: int,
    gift_name: str,
    bot: Bot,
) -> None:
    """Снимает флаг вывода после MAX_ATTEMPTS неудач (перекладываем на админа)."""
    delivery = await gifts_repo.get_delivery_for_update(session, idem)
    if delivery is None or delivery.status != "pending":
        return
    meta = dict(delivery.meta or {})
    attempts = int(meta.get("attempts") or 0)
    if attempts >= MAX_ATTEMPTS and meta.get("withdraw_requested"):
        meta["withdraw_requested"] = False
        meta["withdraw_auto_gave_up"] = True
        delivery.meta = meta
        logger.warning(
            "auto-withdraw gave up on %s after %s attempts (admin will handle)",
            idem,
            attempts,
        )


async def _notify(bot: Bot, user_id: int, text: str) -> None:
    """Личное уведомление игроку (best-effort: ЛС может быть закрыт)."""
    try:
        await bot.send_message(user_id, text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not notify %s about withdraw: %s", user_id, exc)


def setup_gift_delivery_worker(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Регистрирует периодический воркер авто-выдачи выведенных подарков (P2)."""
    scheduler.add_job(
        process_withdraw_queue,
        "interval",
        seconds=WITHDRAW_POLL_SECONDS,
        args=[bot, sessionmaker],
        id="gift_withdraw_worker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
