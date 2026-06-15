"""Магазин Gifts — атомарная покупка, выдача и возврат. Единая точка записи.

Полный цикл (см. GIFTS_SHOP_V1_PLAN.md, TELEGRAM_GIFTS_AUDIT.md):

  ПОКУПКА (одна транзакция БД, как open_case):
    pre-flight под блокировками (каталог + пользователь) → списание ешек
    (change_balance_tx) → reserved+1 → purchase_history → gift_transactions
    (status='pending'). Деньги фиксируются ДО внешнего вызова Telegram.

  ВЫДАЧА (отдельная транзакция + внешний вызов вне БД):
    взять pending-доставку FOR UPDATE → sendGift (адаптер) →
    success: completed, reserved-1, sold_count+1;
    permanent fail: cancelled + возврат ешек + reserved-1;
    retriable fail: оставить pending.

  ВОЗВРАТ:
    cancelled + change_balance_tx(+price) + reserved-1; purchase помечается
    meta.refunded.

Идемпотентность выдачи — idempotency_key UNIQUE + FOR UPDATE строки доставки.
sendGift возвращает только True (нет charge_id) → доказательство выдачи:
status + star_balance_before/after в meta.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.economy_events import EVENT_PURCHASE, EVENT_REWARD
from app.models import GiftCatalog, GiftTransaction, PurchaseHistory, User
from app.repositories import gifts as gifts_repo
from app.services import stars as stars_service
from app.services.economy import change_balance_tx
from app.services.telegram_gifts import DeliveryResult, send_gift
from app.settings.balance import ESHKI_PER_STAR, ITEM_SELL_RATE


def _case_prize_value(
    delivery: GiftTransaction, gift: GiftCatalog | None, eshki_per_star: int = ESHKI_PER_STAR
) -> int:
    """Внутренняя стоимость кейсового приза в ешках (для компенсации возврата).

    Вариант А (см. RELEASE 2.1 / P0): возвращаем ПОЛНУЮ внутреннюю стоимость
    предмета = ``star_cost × ESHKI_PER_STAR``. Источник star_cost: сначала живой
    каталог, затем слепок в ``meta`` доставки (на случай, если позицию каталога
    удалили/переименовали после выпадения). 0 — только если стоимость нигде не
    известна (тогда компенсации не будет, но и отрицательной не уйдём).

    ``eshki_per_star`` может быть переопределён из админки (app_settings:
    economy.eshki_per_star); по умолчанию — код-дефолт ESHKI_PER_STAR.
    """
    star_cost = 0
    if gift is not None:
        star_cost = int(gift.star_cost or 0)
    if star_cost <= 0:
        star_cost = int((delivery.meta or {}).get("star_cost") or 0)
    return max(0, star_cost) * eshki_per_star


def _item_full_value(
    delivery: GiftTransaction, gift: GiftCatalog | None, eshki_per_star: int = ESHKI_PER_STAR
) -> int:
    """Полная стоимость предмета в ешках — ЕДИНАЯ база для продажи (P5/Release 2.2).

    Один понятный игроку курс независимо от источника предмета: базой всегда
    является ЦЕНА МАГАЗИНА (``gift_catalog.price_eshki``) — та же сумма, что
    показывается как «ценность» в инвентаре и цена в магазине. Продать предмет
    можно за ``ITEM_SELL_RATE`` (70%) от этой цены — одинаково для приза кейса и
    покупки магазина. Так не возникает ситуации «в магазине одна цифра, при
    продаже другая».

    Фолбэк (каталог удалён/переименован или price_eshki не задан): внутренняя
    стоимость ``star_cost × ESHKI_PER_STAR`` (из каталога, затем из слепка meta).
    """
    if gift is not None and (gift.price_eshki or 0) > 0:
        return int(gift.price_eshki)
    return _case_prize_value(delivery, gift, eshki_per_star)



def _sell_value(full_value: int, sell_rate: float = ITEM_SELL_RATE) -> int:
    """Сколько ешек получит игрок при ПРОДАЖЕ предмета (P5).

    ``floor(full_value × ITEM_SELL_RATE)``. По умолчанию 70% — убирает дюпы
    экономики (продать дороже покупки нельзя) и создаёт сток ешек. Примеры:
    Роза 250 → 175, Бриллиант 1000 → 700, Premium 10000 → 7000.

    ``sell_rate`` может быть переопределён из админки (app_settings:
    economy.item_sell_rate); по умолчанию — код-дефолт ITEM_SELL_RATE.
    """
    rate = sell_rate if sell_rate >= 0 else ITEM_SELL_RATE
    return int(max(0, full_value) * rate)





@dataclass(frozen=True)
class BuyResult:
    """Итог покупки для рендера в хендлере."""

    status: str  # "ok" | "not_found" | "inactive" | "disabled" | "sold_out" | "not_enough" | "error"
    gift_name: str = ""
    price: int = 0
    balance: int | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class DeliverOutcome:
    """Итог попытки выдачи (для воркера/хендлера)."""

    status: str  # "completed" | "pending" | "cancelled" | "skip"
    refunded: bool = False
    error: str | None = None


@dataclass(frozen=True)
class SellOutcome:
    """Итог продажи предмета (P5)."""

    status: str  # "ok" | "not_found" | "not_pending" | "no_value"
    amount: int = 0          # сколько ешек начислено игроку
    balance: int | None = None  # баланс после продажи
    gift_code: str | None = None
    error: str | None = None



def _channel_meta(channel: str) -> dict:
    """Базовый meta с каналом операции (bot/site/miniapp)."""
    return {"channel": channel}


def _is_shop_purchase(delivery: GiftTransaction) -> bool:
    """True, если доставка — оплаченная покупка магазина (а не приз из кейса).

    Покупка магазина всегда списывает ешки (есть ``transaction_id`` денежной
    проводки) и занимает единицу пула каталога (``reserved+1``). Подарок,
    выигранный в кейсе (:func:`app.features.cases.rewards._grant_tg_gift`),
    ничего не стоил игроку и резерв не занимал — у него ``transaction_id is
    None``. По этому признаку отличаем экономику магазина от призов кейсов:
    выдача/возврат приза НЕ трогает пул каталога и НЕ возвращает ешки.
    """
    return delivery.transaction_id is not None



async def buy_gift(
    session: AsyncSession,
    *,
    user_id: int,
    code: str,
    channel: str = "bot",
) -> BuyResult:
    """Покупает подарок за ешки. Полностью атомарно (commit — middleware).

    После успешной покупки доставка создаётся в статусе ``pending``; реальная
    отправка — отдельным шагом :func:`deliver_gift` (Telegram API нельзя звать
    внутри транзакции БД).
    """
    # --- PRE-FLIGHT под блокировками, без мутаций ---------------------------
    # Глобальный kill-switch магазина из админки (app_settings: shop.enabled).
    # По умолчанию включено. Останавливает покупки во ВСЕХ каналах (бот/сайт),
    # так как buy_gift — единая атомарная точка покупки.
    from app.settings import dynamic

    if not await dynamic.get_bool(session, "shop.enabled", True):
        return BuyResult(status="disabled")

    gift = await gifts_repo.get_gift_for_update(session, code)
    if gift is None:
        return BuyResult(status="not_found")
    if not gift.is_active:
        return BuyResult(status="inactive", gift_name=gift.name)

    # Остаток (NULL stock = безлимит). reserved+sold_count — занятые единицы.
    if gift.stock is not None and (gift.stock - gift.reserved - gift.sold_count) <= 0:
        return BuyResult(status="sold_out", gift_name=gift.name)

    user = await session.get(User, user_id, with_for_update=True)
    if user is None or user.balance < gift.price_eshki:
        return BuyResult(status="not_enough", gift_name=gift.name, price=gift.price_eshki)

    # --- МУТАЦИИ (отказ уже невозможен — строки заблокированы) --------------
    idem = f"giftbuy:{user_id}:{secrets.token_hex(8)}"
    base_meta = _channel_meta(channel)
    base_meta.update(
        {
            "source": "gift_buy",
            "gift": gift.code,
            "star_cost": gift.star_cost,
            "telegram_gift_id": gift.telegram_gift_id,
        }
    )

    # 1) Списать ешки через экономическое ядро (получаем id проводки).
    tx = await change_balance_tx(
        session,
        user_id,
        -gift.price_eshki,
        reason=EVENT_PURCHASE,
        meta={"source": "gift_buy", "gift": gift.code, "channel": channel},
    )

    # 2) Зарезервировать единицу (держит место в пуле до выдачи/возврата).
    await session.execute(
        update(GiftCatalog)
        .where(GiftCatalog.id == gift.id)
        .values(reserved=GiftCatalog.reserved + 1)
    )

    # 3) Запись покупки (деньги). offer_id = id позиции каталога.
    session.add(
        PurchaseHistory(
            user_id=user_id,
            offer_id=gift.id,
            item_code=gift.code,
            price=gift.price_eshki,
            quantity=1,
            source="gift",
            transaction_id=tx.id,
            meta=dict(base_meta),
        )
    )

    # 4) Запись доставки (статус жизненного цикла). pending → выдача позже.
    session.add(
        GiftTransaction(
            kind="tg_gift",
            gift_type="system",
            sender_user_id=None,
            recipient_user_id=user_id,
            item_code=gift.code,
            quantity=1,
            status="pending",
            idempotency_key=idem,
            transaction_id=tx.id,
            meta=dict(base_meta),
        )
    )

    return BuyResult(
        status="ok",
        gift_name=gift.name,
        price=gift.price_eshki,
        balance=user.balance,
        idempotency_key=idem,
    )


async def _refund(
    session: AsyncSession,
    *,
    delivery: GiftTransaction,
    gift_code: str,
    price: int,
    channel: str,
    reason_error: str | None,
) -> None:
    """Отмена доставки с компенсацией игроку.

    Покупка магазина: возврат уплаченной цены (``price``) + освобождение резерва
    каталога (reserved-1).

    Приз кейса (P0): игрок ешки не платил и резерв не занимал, но раньше при
    отмене не получал НИЧЕГО — приз просто пропадал. Теперь возвращаем ПОЛНУЮ
    внутреннюю стоимость предмета (``star_cost × ESHKI_PER_STAR``, Вариант А).
    Пул каталога не трогаем (приз его не занимал — иначе украли бы резерв
    реального покупателя). Источник ешек — экономическое ядро (проводка reward),
    эмиссия фиксируется в леджере как и любая награда.
    """
    meta = dict(delivery.meta or {})
    if _is_shop_purchase(delivery):
        # Покупка магазина: вернуть ешки и освободить место в пуле.
        refund_tx = await change_balance_tx(
            session,
            delivery.recipient_user_id,
            price,
            reason=EVENT_REWARD,
            meta={
                "source": "gift_refund",
                "gift": gift_code,
                "of_transaction": delivery.transaction_id,
                "channel": channel,
            },
        )
        meta.update({"refunded": True, "refund_transaction_id": refund_tx.id})
        # Освободить место в пуле (reserved-1, не ниже нуля).
        await session.execute(
            update(GiftCatalog)
            .where(GiftCatalog.code == gift_code)
            .where(GiftCatalog.reserved > 0)
            .values(reserved=GiftCatalog.reserved - 1)
        )
    else:
        # Приз кейса: компенсируем полную внутреннюю стоимость предмета.
        gift = await gifts_repo.get_gift_by_code(session, gift_code)
        compensation = _case_prize_value(delivery, gift)
        meta["case_prize_cancelled"] = True
        if compensation > 0:
            refund_tx = await change_balance_tx(
                session,
                delivery.recipient_user_id,
                compensation,
                reason=EVENT_REWARD,
                meta={
                    "source": "case_prize_refund",
                    "gift": gift_code,
                    "channel": channel,
                },
            )
            meta.update(
                {
                    "refunded": True,
                    "refund_amount": compensation,
                    "refund_transaction_id": refund_tx.id,
                }
            )
        else:
            # Стоимость нигде не известна — компенсировать нечем (редкий
            # мисконфиг каталога). Помечаем явно, чтобы было видно в meta.
            meta["refund_skipped"] = "unknown_value"

    if reason_error:
        meta["error"] = reason_error
    delivery.status = "cancelled"
    delivery.meta = meta




async def deliver_gift(
    session: AsyncSession,
    bot: Bot,
    *,
    idempotency_key: str,
    enabled: bool,
    channel: str = "bot",
    recipient_override: int | None = None,
) -> DeliverOutcome:
    """Пытается выдать оплаченный подарок (pending → completed/cancelled).

    Идемпотентно: строка доставки берётся FOR UPDATE; уже completed/cancelled —
    выходим. Внешний вызов sendGift делается ПОСЛЕ блокировки, но его результат
    применяется здесь же (одна транзакция). При временной ошибке оставляем
    pending; при постоянной — отменяем с возвратом ешек.

    ``recipient_override`` — отправить РЕАЛЬНЫЙ подарок другому Telegram-юзеру
    («Подарить другу по @username»). Сам подарок уходит на этот user_id, но
    владение/возврат/леджер остаются у плательщика (delivery.recipient_user_id):
    при постоянной ошибке ешки вернутся плательщику, а в meta фиксируется
    ``gifted_to``. None — обычная выдача себе.
    """
    delivery = await gifts_repo.get_delivery_for_update(session, idempotency_key)
    if delivery is None:
        return DeliverOutcome(status="skip", error="delivery_not_found")
    if delivery.status != "pending":
        # Уже обработано (повторный вызов) — ничего не делаем.
        return DeliverOutcome(status="skip")

    # Глобальный kill-switch выдачи из админки (app_settings: gifts.enabled),
    # поверх env-флага GIFTS_DELIVERY_ENABLED. Любой «выкл» оставляет доставку в
    # pending (как и при выключенном env) — ешки не теряются, выдать можно позже.
    from app.settings import dynamic

    if enabled and not await dynamic.get_bool(session, "gifts.enabled", True):
        enabled = False

    gift_code = delivery.item_code or ""
    gift = await gifts_repo.get_gift_by_code(session, gift_code)
    star_cost = int((delivery.meta or {}).get("star_cost") or 0)
    telegram_gift_id = (delivery.meta or {}).get("telegram_gift_id") or (
        gift.telegram_gift_id if gift else None
    )
    price = gift.price_eshki if gift else 0

    # Кому реально шлём: другу (override) или себе (плательщику).
    target_user_id = recipient_override or delivery.recipient_user_id

    # Внешний вызов (вне транзакции БД по смыслу; ошибки изолированы адаптером).
    result: DeliveryResult = await send_gift(
        bot,
        user_id=target_user_id,
        telegram_gift_id=telegram_gift_id or "",
        star_cost=star_cost,
        enabled=enabled,
    )

    meta = dict(delivery.meta or {})
    if result.ok:
        meta.update(
            {
                "api_ok": True,
                "star_balance_before": result.star_balance_before,
                "star_balance_after": result.star_balance_after,
            }
        )
        if recipient_override:
            meta["gifted_to"] = recipient_override
        delivery.status = "completed"
        delivery.meta = meta

        # Реализовать единицу: reserved-1, sold_count+1 — ТОЛЬКО для покупки
        # магазина. Приз кейса резерв не занимал и в продажах каталога не
        # учитывается — пул не трогаем (иначе reserved уйдёт в минус / в
        # sold_count попадёт несуществующая продажа).
        if _is_shop_purchase(delivery):
            await session.execute(
                update(GiftCatalog)
                .where(GiftCatalog.code == gift_code)
                .where(GiftCatalog.reserved > 0)
                .values(
                    reserved=GiftCatalog.reserved - 1,
                    sold_count=GiftCatalog.sold_count + 1,
                )
            )
        # Зафиксировать РАСХОД Stars в едином леджере (источник правды по Stars).

        # ref = idempotency_key доставки → расход однозначно связан с выдачей.
        if star_cost > 0:
            await stars_service.record_out(
                session,
                amount_stars=star_cost,
                reason="gift_send",
                user_id=delivery.recipient_user_id,
                ref=idempotency_key,
                source=channel,
                balance_after=result.star_balance_after,
                meta={"gift": gift_code},
            )
        return DeliverOutcome(status="completed")


    if result.retriable:
        # Временная неудача: оставляем pending, копим попытки.
        attempts = int(meta.get("attempts") or 0) + 1
        meta.update({"attempts": attempts, "last_error": result.error})
        delivery.meta = meta
        return DeliverOutcome(status="pending", error=result.error)

    # Постоянная неудача: отмена + возврат ешек.
    await _refund(
        session,
        delivery=delivery,
        gift_code=gift_code,
        price=price,
        channel=channel,
        reason_error=result.error,
    )
    return DeliverOutcome(status="cancelled", refunded=True, error=result.error)


async def claim_gift_by_token(
    session: AsyncSession,
    bot: Bot,
    *,
    claim_token: str,
    claimer_user_id: int,
    enabled: bool,
    channel: str = "bot",
) -> DeliverOutcome:
    """Выдаёт подарок по claim-ссылке тому, кто открыл `/start gift_<token>`.

    Сценарий «Подарить другу по ссылке» (для тех, кто НЕ запускал бота заранее):
    отправитель создал pending-доставку с ``meta.claim_token``; получатель
    открывает бота по ссылке, мы находим доставку по токену и реально шлём
    подарок ему (``recipient_override = claimer``). Владение/возврат остаются у
    плательщика. Идемпотентно: доставка берётся FOR UPDATE по токену.

    Возврат:
      * skip + 'claim_not_found' — токена нет или подарок уже забран/обработан;
      * далее как deliver_gift (completed/pending/cancelled).
    """
    delivery = await gifts_repo.get_delivery_by_claim_token(session, claim_token)
    if delivery is None:
        return DeliverOutcome(status="skip", error="claim_not_found")

    # Доставку нашли по токену — выдаём её получателю через общий конвейер.
    return await deliver_gift(
        session,
        bot,
        idempotency_key=delivery.idempotency_key,
        enabled=enabled,
        channel=channel,
        recipient_override=claimer_user_id,
    )


async def refund_gift(

    session: AsyncSession,
    *,
    idempotency_key: str,
    channel: str = "bot",
    reason: str | None = "manual",
) -> DeliverOutcome:
    """Ручной возврат pending-доставки (например, админом). Идемпотентно."""
    delivery = await gifts_repo.get_delivery_for_update(session, idempotency_key)
    if delivery is None:
        return DeliverOutcome(status="skip", error="delivery_not_found")
    if delivery.status != "pending":
        return DeliverOutcome(status="skip")

    gift_code = delivery.item_code or ""
    gift = await gifts_repo.get_gift_by_code(session, gift_code)
    price = gift.price_eshki if gift else 0
    await _refund(
        session,
        delivery=delivery,
        gift_code=gift_code,
        price=price,
        channel=channel,
        reason_error=reason,
    )
    return DeliverOutcome(status="cancelled", refunded=True, error=reason)


async def sell_gift(
    session: AsyncSession,
    *,
    idempotency_key: str,
    user_id: int,
    channel: str = "bot",
) -> SellOutcome:
    """Продаёт pending-предмет игрока за ешки (P5). Идемпотентно, атомарно.

    Игрок продаёт ещё не выданный предмет (приз кейса или покупку магазина) и
    получает ``ITEM_SELL_RATE`` (по умолчанию 70%) от его полной внутренней
    стоимости. Доставка переводится в ``cancelled`` (предмет «израсходован»):

    * покупка магазина — освобождаем резерв каталога (reserved-1), как при
      возврате, чтобы место в пуле вернулось;
    * приз кейса — пул не трогаем (приз его не занимал).

    Деньги начисляются через экономическое ядро (проводка reward) — эмиссия
    фиксируется в леджере. Строка доставки берётся FOR UPDATE: двойной клик и
    гонки сериализуются, продать дважды один предмет нельзя.

    Проверяем владельца: продать можно только СВОЙ предмет (``recipient`` ==
    ``user_id``) — защита от продажи чужого приза через подменённый ключ.
    """
    delivery = await gifts_repo.get_delivery_for_update(session, idempotency_key)
    if delivery is None:
        return SellOutcome(status="not_found", error="delivery_not_found")
    if delivery.recipient_user_id != user_id:
        # Не твой предмет — ведём себя как «не найдено» (не раскрываем чужое).
        return SellOutcome(status="not_found", error="not_owner")
    if delivery.status != "pending":
        return SellOutcome(status="not_pending", gift_code=delivery.item_code)

    gift_code = delivery.item_code or ""
    gift = await gifts_repo.get_gift_by_code(session, gift_code)
    # Курсы продажи редактируются из админки без деплоя (app_settings):
    #   economy.eshki_per_star — фолбэк-оценка приза кейса по star_cost;
    #   economy.item_sell_rate — доля стоимости, возвращаемая при продаже.
    from app.settings import dynamic

    eshki_per_star = await dynamic.get_int(session, "economy.eshki_per_star", ESHKI_PER_STAR)
    sell_rate = await dynamic.get_float(session, "economy.item_sell_rate", ITEM_SELL_RATE)
    full_value = _item_full_value(delivery, gift, eshki_per_star)
    amount = _sell_value(full_value, sell_rate)
    if amount <= 0:
        # Стоимость неизвестна — продавать нечего (мисконфиг каталога).
        return SellOutcome(status="no_value", gift_code=gift_code, error="unknown_value")

    # Начислить ешки за продажу.
    sell_tx = await change_balance_tx(
        session,
        user_id,
        amount,
        reason=EVENT_REWARD,
        meta={
            "source": "item_sell",
            "gift": gift_code,
            "full_value": full_value,
            "channel": channel,
        },
    )

    # Освободить резерв каталога для покупки магазина (приз кейса резерв не
    # занимал — не трогаем).
    if _is_shop_purchase(delivery):
        await session.execute(
            update(GiftCatalog)
            .where(GiftCatalog.code == gift_code)
            .where(GiftCatalog.reserved > 0)
            .values(reserved=GiftCatalog.reserved - 1)
        )

    meta = dict(delivery.meta or {})
    meta.update(
        {
            "sold": True,
            "sell_amount": amount,
            "sell_full_value": full_value,
            "sell_transaction_id": sell_tx.id,
            "sell_channel": channel,
        }
    )
    delivery.status = "cancelled"
    delivery.meta = meta

    user = await session.get(User, user_id)
    return SellOutcome(
        status="ok",
        amount=amount,
        balance=user.balance if user else None,
        gift_code=gift_code,
    )


async def complete_gift_manually(
    session: AsyncSession,
    *,
    idempotency_key: str,
    admin_user_id: int,

    channel: str = "bot",
) -> DeliverOutcome:
    """Ручная отметка pending-доставки как ВЫДАННОЙ (админом).

    Сценарий: автоматическая выдача через Telegram не сработала (выдача
    выключена, нет gift_id, не хватает Stars), и админ отправил подарок
    вручную. Эта функция приводит леджер в то же состояние, что и успешный
    :func:`deliver_gift`:

      pending → completed, reserved-1, sold_count+1.

    Деньги игрока не трогаем (покупка уже списала ешки и зафиксирована в
    purchase_history), поэтому экономическая статистика остаётся корректной,
    а подарок считается отправленным и попадает в аналитику как ``completed``.

    Для приза кейса (нет денежной проводки, резерв каталога не занимался —
    см. :func:`_is_shop_purchase`) пул каталога НЕ трогаем: достаточно перевести
    доставку в ``completed``. Так одна команда ``/gifts_done`` закрывает и
    купленные, и выигранные в кейсе подарки.

    Stars здесь НЕ списываем: ручная выдача делается вне бота, точного расхода
    Stars у нас нет — фиксируем только факт ручной выдачи в meta. Идемпотентно.
    """
    delivery = await gifts_repo.get_delivery_for_update(session, idempotency_key)
    if delivery is None:
        return DeliverOutcome(status="skip", error="delivery_not_found")
    if delivery.status != "pending":
        # Уже обработано (completed/cancelled) — повторно ничего не делаем.
        return DeliverOutcome(status="skip")

    gift_code = delivery.item_code or ""
    meta = dict(delivery.meta or {})
    meta.update(
        {
            "manual_delivery": True,
            "manual_by_admin": admin_user_id,
            "manual_channel": channel,
        }
    )
    delivery.status = "completed"
    delivery.meta = meta

    # Реализовать единицу: reserved-1, sold_count+1 — ТОЛЬКО для покупки
    # магазина. Приз кейса резерв не занимал и продажей каталога не является.
    if _is_shop_purchase(delivery):
        await session.execute(
            update(GiftCatalog)
            .where(GiftCatalog.code == gift_code)
            .where(GiftCatalog.reserved > 0)
            .values(
                reserved=GiftCatalog.reserved - 1,
                sold_count=GiftCatalog.sold_count + 1,
            )
        )
    return DeliverOutcome(status="completed")



