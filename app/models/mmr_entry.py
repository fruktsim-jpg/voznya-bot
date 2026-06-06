"""MMR — единый игровой рейтинг игрока (общий прогресс в экосистеме Возни).

``mmr_entries`` — журнал всех изменений рейтинга (источник правды). Одна
строка = одно изменение: кому начислили/списали, сколько, за что (source) и
когда. Текущий MMR игрока — производное значение ``SUM(amount)`` по его
строкам, поэтому его всегда можно пересчитать из истории.

Это ОТДЕЛЬНАЯ система прогресса. Она НЕ связана с ешками (``users.balance``/
``transactions``), репутацией (``reputation_entries``), счётчиком сообщений
(``users.messages_count``), магазином, инвентарём, подарками, Combot и OIDC.

MMR — только показатель прогресса: его нельзя купить за ешки, передать,
обменять или использовать как валюту. Сообщения MMR не дают; рейтинг растёт
только за игровые действия (клад, дуэль, ферма, ачивки, ивенты, награды).

Внутри кода величина изменения может называться XP-подобными терминами, но
в пользовательском интерфейсе игрок видит ТОЛЬКО «MMR».
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MmrEntry(Base):
    """Одно изменение рейтинга MMR (начисление или списание)."""

    __tablename__ = "mmr_entries"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Кому начислили/списали рейтинг.
    player_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Величина изменения: > 0 — начисление, < 0 — списание (админ).
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    # Категория источника: treasure / duel / farm / achievement / event / admin.
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # Уточнение причины (код ачивки, "win"/"participation", описание ивента...).
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Текущий MMR и топы: агрегаты по игроку.
        Index("ix_mmr_player", "player_id"),
        # Хронология/история игрока (новые сверху).
        Index("ix_mmr_player_created", "player_id", "created_at"),
    )
