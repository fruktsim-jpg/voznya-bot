"""Резолв имён игроков для контекста друна.

Друн должен говорить о людях по нику, а не по числовому ``user_id``. Этот
модуль берёт набор id и возвращает карту ``id → читаемое имя`` одним запросом
(без N+1). Имя — ``display_name`` пользователя (first_name → @username → id).

Только чтение. Любой сбой деградирует к строковому id, чтобы контекст не падал.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.models import User

logger = get_logger(__name__)


async def resolve_names(
    session: AsyncSession, user_ids: Iterable[int | None]
) -> dict[int, str]:
    """Карта ``user_id → display_name`` для всех непустых id одним запросом."""
    ids = {uid for uid in user_ids if uid is not None}
    if not ids:
        return {}
    try:
        rows = (
            await session.execute(select(User).where(User.user_id.in_(ids)))
        ).scalars().all()
        return {u.user_id: u.display_name() for u in rows}
    except Exception:  # noqa: BLE001
        logger.debug("resolve_names failed", exc_info=True)
        return {}


def name_for(names: dict[int, str], user_id: int | None) -> str:
    """Имя из карты или человекочитаемый фолбэк, если игрок неизвестен."""
    if user_id is None:
        return "кто-то"
    return names.get(user_id) or f"игрок#{user_id}"


async def resolve_person_hints(
    session: AsyncSession, user_ids: Iterable[int | None]
) -> dict[int, str]:
    """Карта ``user_id → краткая подсказка о человеке`` (пол/как звать) одним
    запросом по ``ai_profiles``.

    Нужна, чтобы друн НЕ путал пол и имя людей, упомянутых в живом чате/событиях,
    а не только текущего собеседника (раньше пол/алиасы резолвились лишь для
    subject). Возвращает только тех, у кого есть осмысленная подсказка.
    """
    from app.models import AiProfile

    ids = {uid for uid in user_ids if uid is not None}
    if not ids:
        return {}
    try:
        rows = (
            await session.execute(
                select(AiProfile.user_id, AiProfile.data)
                .where(AiProfile.user_id.in_(ids))
            )
        ).all()
    except Exception:  # noqa: BLE001
        logger.debug("resolve_person_hints failed", exc_info=True)
        return {}
    out: dict[int, str] = {}
    for uid, data in rows:
        data = data or {}
        bits: list[str] = []
        gender = (data.get("gender") or "").strip()
        if gender == "male":
            bits.append("м")
        elif gender == "female":
            bits.append("ж")
        pref = (data.get("preferred_name") or "").strip()
        if pref:
            bits.append(f"звать {pref}")
        if bits:
            out[uid] = ", ".join(bits)
    return out
