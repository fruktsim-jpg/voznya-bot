"""Журнал прогонов исторического импорта из Combot.

Каждый запуск одноразового импорта пишет сюда одну строку: когда, какой
диапазон тянули, сколько строк записали, статус и ошибка (если была). Нужен
для идемпотентности (видно, что импорт уже делался) и для аудита.

Не трогает users/баланс/инвентарь/shop/gift. Связи с combot_*-таблицами
логические (по ``id`` → ``import_run_id``), без FK.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Статусы прогона.
COMBOT_IMPORT_STATUSES = ("running", "success", "failed")


class CombotImportRun(Base):
    """Один запуск исторического импорта Combot."""

    __tablename__ = "combot_import_runs"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Статус (один из COMBOT_IMPORT_STATUSES).
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running"
    )
    # Диапазон выгрузки (Unix ms), как передавали в API.
    range_from_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    range_to_ms: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    # Сколько строк записали по каждому набору.
    users_imported: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    days_imported: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    heatmap_cells_imported: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Кто запустил (user_id админа) — NULL для CLI/системного запуска.
    started_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Текст ошибки при status='failed'.
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Произвольные детали прогона (итоги по API, версии и т.п.).
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
