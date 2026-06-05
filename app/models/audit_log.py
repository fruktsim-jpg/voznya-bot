"""Аудит-лог действий администраторов.

Любое значимое действие администратора (выдача/снятие ешек, выдача/удаление
предмета, изменение роли, бан/разбан) пишет сюда неизменяемую (append-only)
строку. Это:

* единая лента «кто что сделал» для разбора инцидентов;
* доказуемость действий (снимок роли и причины на момент действия);
* основа раздела ``Logs`` в админ-панели.

Таблица только дополняется — строки не редактируются и не удаляются (кроме
 retention-чистки очень старых записей, если понадобится). Денежные операции
дублируются в ``transactions`` (леджер валюты); ``audit_log`` фиксирует
управленческий контекст (кто из админов инициировал и почему).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    """Одна запись аудита административного действия."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # Кто совершил действие (user_id админа).
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Снимок роли актора на момент действия (роль могли позже изменить).
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Машинный код действия в формате "<домен>.<глагол>", например:
    # economy.add, economy.remove, inventory.grant, inventory.revoke,
    # role.change, player.ban, player.unban.
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    # Над кем действие (user_id игрока), если применимо.
    target_user_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    # Тип цели и её id (предмет, роль, транзакция…), если цель не игрок.
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Сумма для денежных операций (ешки): + начисление / − списание.
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Свободная причина/комментарий администратора.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Произвольные детали (старое/новое значение роли, payload предмета и т.п.).
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # IP, с которого выполнено действие через панель (для бота — NULL).
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Лента по актору и по затронутому игроку — частые выборки в панели.
        Index("ix_audit_log_actor", "actor_user_id", "created_at"),
        Index("ix_audit_log_target", "target_user_id", "created_at"),
        Index("ix_audit_log_action", "action", "created_at"),
    )
