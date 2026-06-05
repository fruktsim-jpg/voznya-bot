"""Связь между OIDC-аккаунтом Telegram Login и игроком (users.user_id).

OIDC-провайдер Telegram (oauth.telegram.org) выдаёт claim ``sub``, который НЕ
является Telegram user_id (это непрозрачный pairwise-идентификатор, привязанный
к client_id, и он больше 2^53). Поэтому напрямую сопоставить ``sub`` с
``users.user_id`` нельзя.

Таблица ``account_links`` хранит подтверждённое соответствие
``oidc_sub -> user_id``. Связь создаётся ботом ОДИН раз, после того как
пользователь подтвердил владение Telegram-аккаунтом, открыв deep-link и нажав
«Start» (бот видит настоящий ``message.from_user.id``).

Таблица отдельная от ``users``: экономика, профили и роли не меняются.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AccountLink(Base):
    """Подтверждённое соответствие OIDC ``sub`` ↔ Telegram ``user_id``."""

    __tablename__ = "account_links"

    # OIDC sub хранится как строка: значение превышает 2^53 и не помещается
    # безопасно в число (ни в JS Number, ни смысла нет в BigInteger-арифметике).
    oidc_sub: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
