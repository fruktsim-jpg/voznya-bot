"""Модель отложенного удаления сообщений.

Чтобы чат оставался чистым, бот удаляет служебные сообщения через
заданное время. Задачи хранятся в БД, поэтому переживают рестарт бота:
при старте незавершённые удаления загружаются заново.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ScheduledDeletion(Base):
    """Запланированное удаление сообщения."""

    __tablename__ = "scheduled_deletions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delete_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_deletions_pending", "done", "delete_at"),
    )
