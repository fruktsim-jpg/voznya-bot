"""Роль администратора платформы Возни.

Фундамент админ-платформы: таблица назначает Telegram-игроку
(``users.user_id``) одну из ролей с разным набором прав. Роли проверяются и
ботом (админ-команды), и сайтом/Mini App (админ-панель) — единый источник
правды о том, кто и что может делать.

Почему отдельная таблица, а не поле в ``users``:

* роли — это про доступ к платформе, а не игровая характеристика;
* большинство игроков ролей не имеет (таблица маленькая, разрежённая);
* назначения нужно аудировать (кто выдал роль) — см. ``audit_log``.

Связь с существующим списком ``ADMIN_IDS`` (env): он остаётся «аварийным»
суперпользователем уровня кода (bootstrap первого ``owner``), но повседневные
права берутся отсюда. Подробнее — ``ADMIN_PLATFORM.md``.

Внешних ключей намеренно нет: в проекте принято не связывать таблицы FK
(см. ``transactions.user_id`` — тоже без FK), чтобы упростить миграции и
удаление игроков.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Допустимые роли. Хранятся строкой (а не Enum БД), чтобы добавлять роли без
# ALTER TYPE; набор валидируется на уровне приложения. Иерархия по убыванию
# прав: owner > admin > moderator > support.
ADMIN_ROLES = ("owner", "admin", "moderator", "support")


class AdminRole(Base):
    """Назначенная игроку роль на админ-платформе.

    Один игрок — максимум одна роль (``user_id`` это PK). Повышение/понижение
    роли — это UPDATE строки, и оно обязано логироваться в ``audit_log``
    (action ``role.change``).
    """

    __tablename__ = "admin_roles"

    # Telegram user_id игрока, которому выдана роль (PK, не autoincrement —
    # как и в users).
    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    # Одна из ADMIN_ROLES. Индексируется для выборки «все модераторы» и т.п.
    role: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # Кто выдал роль (user_id админа). NULL — назначено системой/bootstrap.
    granted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
