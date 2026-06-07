"""Леджер Telegram Stars — единый источник правды по движению Stars бота.

Зачем нужен (см. STARS_FUNDING_GUIDE §5, ECONOMY_LOGGING_AUDIT): баланс Stars
живёт у Telegram (`getMyStarBalance`), но Telegram НЕ хранит нашу бизнес-историю —
кто пополнил, за что списали, какой charge_id. Без собственного леджера нельзя:

* корректно считать P&L (доход Stars от топ-апов/донатов vs расход на `sendGift`);
* восстановить любую операцию через полгода;
* дедуплицировать входящие платежи (по `telegram_payment_charge_id`).

Это журнал-источник правды на актив «Stars» — параллель ``transactions`` для
ешек. Каждая строка — одно движение:

* ``direction='in'``  — приход Stars боту (оплата XTR-инвойса: топ-ап, позже донат);
* ``direction='out'`` — расход Stars ботом (отправка Gift через `sendGift`).

Связи логические (без FK): ``charge_id`` — telegram_payment_charge_id входящего
платежа (UNIQUE → защита от двойного зачисления); ``ref`` — ссылка на связанную
сущность (например, idempotency_key доставки Gift при расходе).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Направление движения Stars.
STARS_DIRECTIONS = ("in", "out")

# Причина. Расширяемо без миграции (это не CHECK), но фиксируем словарь:
#   topup     — пополнение баланса бота владельцем (XTR-инвойс);
#   donation  — донат игрока Stars→ешки (будущий этап, та же точка приёма);
#   gift_send — расход на отправку Telegram Gift (`sendGift`);
#   refund    — возврат входящего платежа (refundStarPayment).
STARS_REASONS = ("topup", "donation", "gift_send", "refund")


class StarsLedger(Base):
    """Одно движение Telegram Stars (приход или расход)."""

    __tablename__ = "stars_ledger"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # 'in' — приход боту, 'out' — расход бота.
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    # Количество Stars (всегда положительное; знак задаёт direction).
    amount_stars: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Причина (topup/donation/gift_send/refund).
    reason: Mapped[str] = mapped_column(String(16), nullable=False)

    # Кто инициировал/кого касается (плательщик при 'in', получатель Gift при
    # 'out'). NULL — системная операция.
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # telegram_payment_charge_id входящего платежа — UNIQUE: один платёж не может
    # быть зачислен дважды (идемпотентность приёма). Для 'out' обычно NULL
    # (sendGift не возвращает charge_id).
    charge_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True
    )

    # Ссылка на связанную сущность (например, idempotency_key Gift-доставки при
    # расходе). Без FK — конвенция проекта.
    ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Источник операции: bot / site / miniapp (канал инициации).
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="bot"
    )

    # Снимок баланса Stars бота после операции (если удалось получить через
    # getMyStarBalance) — косвенная сверка с внешним балансом.
    balance_after: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_stars_ledger_created", "created_at"),
        Index("ix_stars_ledger_user", "user_id", "created_at"),
        Index("ix_stars_ledger_reason", "reason", "created_at"),
        CheckConstraint(
            "direction IN ('in','out')", name="ck_stars_direction"
        ),
        CheckConstraint("amount_stars > 0", name="ck_stars_amount_positive"),
    )
