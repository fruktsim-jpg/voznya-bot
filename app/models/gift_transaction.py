"""Подарки — передача активов между игроками (и от системы/администрации).

``gift_transactions`` фиксирует факт передачи предмета или ешек. Это
бизнес-журнал подарков — он НЕ владелец активов и НЕ леджер денег:

* владение предметами остаётся в ``inventory`` (передача = revoke у отправителя +
  grant получателю в одной транзакции, обе записи в ``inventory_history``);
* движение ешек остаётся в ``transactions`` (две проводки: −у отправителя,
  +получателю), связь по ``transaction_id``;
* админ/системные подарки дополнительно пишут ``audit_log`` (связь по
  ``audit_id``).

Защита от двойной отправки — уникальный ``idempotency_key``: повторный запрос с
тем же ключом не создаст второй подарок.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Что дарится. ``tg_gift`` — реальный Telegram Gift (внешний актив, выдаётся через
# Bot API sendGift; не предмет инвентаря и не ешки) — см. TELEGRAM_GIFTS_AUDIT.md.
GIFT_KINDS = ("item", "currency", "tg_gift")


# Происхождение подарка.
GIFT_TYPES = ("player", "system", "admin")

# Жизненный статус (на фундаменте подарок завершается сразу; статус оставлен
# под будущие «pending/claim»-сценарии без миграции).
GIFT_STATUSES = ("completed", "pending", "cancelled")


class GiftTransaction(Base):
    """Одна передача актива: предмет или ешки."""

    __tablename__ = "gift_transactions"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Что дарим: item / currency.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Происхождение: player (игрок→игрок) / system / admin.
    gift_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="player"
    )

    # Отправитель: user_id игрока/админа. NULL — системный подарок.
    sender_user_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    # Получатель (обязателен).
    recipient_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Для kind='item': код предмета и количество.
    item_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Для kind='currency': сумма ешек (> 0).
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="completed"
    )

    # Защита от двойной отправки: один ключ — один подарок.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )

    # Связи с леджерами (без FK).
    transaction_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    audit_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_gift_recipient", "recipient_user_id", "created_at"),
        Index("ix_gift_sender", "sender_user_id", "created_at"),
        # Нельзя подарить актив самому себе.
        CheckConstraint(
            "sender_user_id IS NULL OR sender_user_id <> recipient_user_id",
            name="ck_gift_not_self",
        ),
        # Сумма валютного подарка строго положительна.
        CheckConstraint(
            "amount IS NULL OR amount > 0", name="ck_gift_amount_positive"
        ),
        # Целостность по виду: item ⇒ есть item_code; currency ⇒ есть amount.
        CheckConstraint(
            "(kind = 'item' AND item_code IS NOT NULL) OR "
            "(kind = 'currency' AND amount IS NOT NULL)",
            name="ck_gift_kind_payload",
        ),
    )
