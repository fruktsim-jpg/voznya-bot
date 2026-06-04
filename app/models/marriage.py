"""Модель брака между двумя пользователями."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Marriage(Base):
    """Запись о браке.

    Активным считается брак с ``divorced_at IS NULL``. У одного пользователя
    может быть только один активный брак (проверяется в сервисе).
    """

    __tablename__ = "marriages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id_1: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id_2: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    married_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    divorced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_marriages_active", "divorced_at"),
    )
