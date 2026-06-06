"""Репутация — отдельный социальный рейтинг сообщества.

``reputation_entries`` — журнал всех изменений репутации (источник правды).
Одна строка = одно изменение: кто оценил, кого, +1/-1, по какой фразе и когда.

Это ОТДЕЛЬНАЯ система: она НЕ связана с ешками (``users.balance``/
``transactions``), XP, счётчиком сообщений (``users.messages_count``),
магазином, инвентарём, подарками, Combot и OIDC. Текущая репутация игрока —
производное значение: ``SUM(value)`` по его строкам, поэтому её всегда можно
пересчитать из истории.

Антиспам (не чаще 1 раза в 12 часов на одну пару «оценивающий → оценённый»)
проверяется по этому же журналу — отдельная таблица кулдаунов не нужна.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReputationEntry(Base):
    """Одно изменение репутации (+1 или -1) от игрока игроку."""

    __tablename__ = "reputation_entries"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Кого оценили (получатель репутации).
    target_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Кто оценил (автор изменения).
    giver_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Знак изменения: +1 (поддержка) или -1 (неодобрение).
    value: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Фраза-алиас, вызвавшая изменение («спасибо», «+реп», «кринж», ...).
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Только +1 / -1.
        CheckConstraint("value IN (-1, 1)", name="ck_reputation_value"),
        # Нельзя оценить самого себя.
        CheckConstraint(
            "giver_user_id <> target_user_id", name="ck_reputation_not_self"
        ),
        # Текущая репутация и топы: агрегаты по получателю.
        Index("ix_reputation_target", "target_user_id"),
        # Антиспам: последнее изменение конкретной пары giver→target.
        Index(
            "ix_reputation_pair",
            "giver_user_id",
            "target_user_id",
            "created_at",
        ),
    )
