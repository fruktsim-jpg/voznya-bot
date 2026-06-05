"""Одноразовый запрос на привязку OIDC-аккаунта к Telegram-игроку.

Поток (Variant 2 — без кросс-язычной криптографии):

1. Пользователь входит на сайт через Telegram OIDC. Сайт получает ``sub``,
   но для него ещё нет связи в ``account_links``.
2. Сайт генерирует случайный одноразовый ``token`` и пишет сюда строку
   (token -> oidc_sub, expires_at). Это ЕДИНСТВЕННАЯ таблица, в которую пишет
   сайт; игровые таблицы остаются read-only.
3. Сайт показывает deep-link ``t.me/<bot>?start=link_<token>``.
4. Пользователь открывает его — бот в личке видит НАСТОЯЩИЙ
   ``message.from_user.id``, находит запись по ``token``, проверяет срок,
   создаёт ``account_links(oidc_sub -> user_id)`` и удаляет запрос.

Токен одноразовый и с TTL: перехват/replay невозможны после первого
использования или истечения срока.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OidcLinkRequest(Base):
    """Незавершённый запрос привязки: одноразовый ``token`` → ``oidc_sub``."""

    __tablename__ = "oidc_link_requests"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    oidc_sub: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
