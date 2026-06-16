"""Варн (предупреждение) игрока — append-only история.

Каждый ``/warn`` создаёт строку. Снятие варна (``/unwarn``) или его «протухание»
по TTL помечает строку ``active=False`` — строки не удаляются, чтобы история
модерации оставалась полной. Количество АКТИВНЫХ варнов денормализовано в
``user_moderation.warn_count`` для быстрого порога авто-мьюта.

Внешних ключей намеренно нет (соглашение проекта).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Text, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ModWarning(Base):
    """Одно предупреждение, выданное игроку."""

    __tablename__ = "mod_warnings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_mod_warnings_user_active", "user_id", "active", "created_at"),
    )
