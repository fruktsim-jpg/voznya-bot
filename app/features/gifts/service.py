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
from app.services.economy import change_balance_tx
from app.services.telegram_gifts import DeliveryResult, send_gift


@dataclass(frozen=True)
class BuyResult:
    """Итог покупки для рендера в хендлере."""

    status: str  # "ok" | "not_found" | "inactive" | "sold_out" | "not_enough" | "error"
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


def _channel_meta(channel: str) -> dict:
    """Базовый meta с каналом операции (bot/site/miniapp)."""
    return {"channel": channel}


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
    """Возврат ешек игроку + освобождение резерва + статус cancelled."""
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
    meta = dict(delivery.meta or {})
    meta.update({"refunded": True, "refund_transaction_id": refund_tx.id})
    if reason_error:
        meta["error"] = reason_error
    delivery.status = "cancelled"
    delivery.meta = meta

    # Освободить место в пуле (reserved-1, не ниже нуля).
    await session.execute(
        update(GiftCatalog)
        .where(GiftCatalog.code == gift_code)
        .where(GiftCatalog.reserved > 0)
        .values(reserved=GiftCatalog.reserved - 1)
    )


async def deliver_gift(
    session: AsyncSession,
    bot: Bot,
    *,
    idempotency_key: str,
    enabled: bool,
    channel: str = "bot",
) -> DeliverOutcome:
    """Пытается выдать оплаченный подарок (pending → completed/cancelled).

    Идемпотентно: строка доставки берётся FOR UPDATE; уже completed/cancelled —
    выходим. Внешний вызов sendGift делается ПОСЛЕ блокировки, но его результат
    применяется здесь же (одна транзакция). При временной ошибке оставляем
    pending; при постоянной — отменяем с возвратом ешек.
    """
    delivery = await gifts_repo.get_delivery_for_update(session, idempotency_key)
    if delivery is None:
        return DeliverOutcome(status="skip", error="delivery_not_found")
    if delivery.status != "pending":
        # Уже обработано (повторный вызов) — ничего не делаем.
        return DeliverOutcome(status="skip")

    gift_code = delivery.item_code or ""
    gift = await gifts_repo.get_gift_by_code(session, gift_code)
    star_cost = int((delivery.meta or {}).get("star_cost") or 0)
    telegram_gift_id = (delivery.meta or {}).get("telegram_gift_id") or (
        gift.telegram_gift_id if gift else None
    )
    price = gift.price_eshki if gift else 0

    # Внешний вызов (вне транзакции БД по смыслу; ошибки изолированы адаптером).
    result: DeliveryResult = await send_gift(
        bot,
        user_id=delivery.recipient_user_id,
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
        delivery.status = "completed"
        delivery.meta = meta
        # Реализовать единицу: reserved-1, sold_count+1.
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
