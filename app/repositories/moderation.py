"""Запросы и операции модерации (бан/мьют/варны).

Здесь только работа с БД (состояние модерации, варны, аудит). Решения «можно
ли», парсинг длительностей и применение ограничений через Telegram — в
``app/features/moderation/service.py``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import now_utc
from app.models import AuditLog, ModWarning, UserModeration


async def get_state(
    session: AsyncSession, user_id: int
) -> UserModeration | None:
    """Текущее состояние модерации игрока или None, если строки ещё нет."""
    return await session.get(UserModeration, user_id)


async def _upsert_state(
    session: AsyncSession, user_id: int, values: dict
) -> UserModeration:
    """Создаёт/обновляет строку состояния и возвращает её (после flush)."""
    stmt = (
        pg_insert(UserModeration)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(
            index_elements=[UserModeration.user_id],
            set_={**values, "updated_at": now_utc()},
        )
    )
    await session.execute(stmt)
    await session.flush()
    state = await session.get(UserModeration, user_id)
    assert state is not None
    return state


async def set_ban(
    session: AsyncSession,
    user_id: int,
    banned_until: datetime | None,
    reason: str | None,
    actor_user_id: int | None,
) -> UserModeration:
    """Ставит (или снимает, если banned_until=None) бан игроку."""
    return await _upsert_state(
        session,
        user_id,
        {
            "banned_until": banned_until,
            "ban_reason": reason if banned_until is not None else None,
            "updated_by": actor_user_id,
        },
    )


async def set_mute(
    session: AsyncSession,
    user_id: int,
    muted_until: datetime | None,
    reason: str | None,
    actor_user_id: int | None,
) -> UserModeration:
    """Ставит (или снимает, если muted_until=None) мьют игроку."""
    return await _upsert_state(
        session,
        user_id,
        {
            "muted_until": muted_until,
            "mute_reason": reason if muted_until is not None else None,
            "updated_by": actor_user_id,
        },
    )


async def add_warning(
    session: AsyncSession,
    user_id: int,
    actor_user_id: int | None,
    reason: str | None,
    expires_at: datetime | None,
) -> int:
    """Добавляет варн, пересчитывает активные и возвращает новое число активных."""
    session.add(
        ModWarning(
            user_id=user_id,
            actor_user_id=actor_user_id,
            reason=reason,
            expires_at=expires_at,
            active=True,
        )
    )
    await session.flush()
    return await _recount_active_warns(session, user_id, actor_user_id)


async def clear_warnings(
    session: AsyncSession, user_id: int, actor_user_id: int | None
) -> int:
    """Снимает ВСЕ активные варны игрока. Возвращает сколько сняли."""
    result = await session.execute(
        update(ModWarning)
        .where(ModWarning.user_id == user_id, ModWarning.active.is_(True))
        .values(active=False)
    )
    await _recount_active_warns(session, user_id, actor_user_id)
    return int(result.rowcount or 0)


async def expire_warnings(session: AsyncSession, user_id: int) -> int:
    """Деактивирует протухшие варны (expires_at <= now). Возвращает число активных."""
    await session.execute(
        update(ModWarning)
        .where(
            ModWarning.user_id == user_id,
            ModWarning.active.is_(True),
            ModWarning.expires_at.is_not(None),
            ModWarning.expires_at <= now_utc(),
        )
        .values(active=False)
    )
    return await _recount_active_warns(session, user_id, None)


async def count_active_warns(session: AsyncSession, user_id: int) -> int:
    """Считает активные варны игрока."""
    count = await session.scalar(
        select(func.count())
        .select_from(ModWarning)
        .where(ModWarning.user_id == user_id, ModWarning.active.is_(True))
    )
    return int(count or 0)


async def _recount_active_warns(
    session: AsyncSession, user_id: int, actor_user_id: int | None
) -> int:
    """Пересчитывает активные варны и синхронизирует denormalized warn_count."""
    active = await count_active_warns(session, user_id)
    await _upsert_state(
        session,
        user_id,
        {"warn_count": active, "updated_by": actor_user_id},
    )
    return active


async def write_audit(
    session: AsyncSession,
    *,
    actor_user_id: int,
    actor_role: str | None,
    action: str,
    target_user_id: int | None,
    reason: str | None = None,
    meta: dict | None = None,
) -> None:
    """Пишет строку в audit_log (общая лента «кто что сделал» для панели).

    Источник на стороне бота — ip=NULL, target_type='user' для игроков.
    """
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            action=action,
            target_user_id=target_user_id,
            target_type="user" if target_user_id is not None else None,
            target_id=str(target_user_id) if target_user_id is not None else None,
            reason=reason,
            meta=meta,
            ip=None,
        )
    )


async def get_role(session: AsyncSession, user_id: int) -> str | None:
    """Роль игрока на админ-платформе (admin_roles) или None."""
    from app.models import AdminRole

    row = await session.get(AdminRole, user_id)
    return row.role if row is not None else None


async def list_warnings(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[ModWarning]:
    """Последние варны игрока (для /modinfo), новые сверху."""
    result = await session.execute(
        select(ModWarning)
        .where(ModWarning.user_id == user_id)
        .order_by(ModWarning.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def due_unbans(session: AsyncSession) -> list[int]:
    """user_id, у кого срок бана истёк (banned_until <= now). Для авто-снятия."""
    result = await session.execute(
        select(UserModeration.user_id).where(
            UserModeration.banned_until.is_not(None),
            UserModeration.banned_until <= now_utc(),
        )
    )
    return [row[0] for row in result.all()]


async def due_unmutes(session: AsyncSession) -> list[int]:
    """user_id, у кого срок мьюта истёк (muted_until <= now). Для авто-снятия."""
    result = await session.execute(
        select(UserModeration.user_id).where(
            UserModeration.muted_until.is_not(None),
            UserModeration.muted_until <= now_utc(),
        )
    )
    return [row[0] for row in result.all()]


async def pending_tg(session: AsyncSession) -> list[UserModeration]:
    """Записи, которые сайт изменил и пометил для применения в Telegram.

    Бот читает их в фоновом тике, применяет/снимает ограничения через
    Telegram и сбрасывает флаг (см. clear_tg_pending).
    """
    result = await session.execute(
        select(UserModeration).where(UserModeration.tg_pending.is_(True))
    )
    return list(result.scalars().all())


async def clear_tg_pending(session: AsyncSession, user_id: int) -> None:
    """Сбрасывает флаг ожидания применения в Telegram."""
    await session.execute(
        update(UserModeration)
        .where(UserModeration.user_id == user_id)
        .values(tg_pending=False)
    )
