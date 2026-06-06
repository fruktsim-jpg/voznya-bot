"""Тепловая карта активности из Combot (поле ``analytics.hours``).

Источник — массив троек ``[hour, weekday, count]`` из ``channel_analytics``:
сколько сообщений приходится на каждый час суток (0–23) в разрезе дня недели
(0–6). 24×7 = до 168 ячеек. Снимок на момент импорта, перезаписывается целиком
при повторном прогоне (ключ — пара hour+weekday).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Integer,
    SmallInteger,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CombotActivityHeatmap(Base):
    """Кол-во сообщений в ячейке (час суток × день недели)."""

    __tablename__ = "combot_activity_heatmap"

    # Час суток 0–23.
    hour: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    # День недели 0–6 (как отдаёт Combot).
    weekday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    # Кол-во сообщений в этой ячейке.
    messages: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Логическая ссылка на прогон импорта (без FK).
    import_run_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "hour >= 0 AND hour <= 23", name="ck_combot_heatmap_hour"
        ),
        CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_combot_heatmap_weekday",
        ),
    )
