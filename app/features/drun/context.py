"""Сборка контекста для модели: что друн «видит» перед ответом.

Перед каждым запросом автоматически подмешиваем:
* статистику игрока (баланс, MMR, репутация, дуэли, сообщения) — если запрос
  про конкретного игрока;
* информацию о сезоне (активен ли, топ);
* последние события мира (``world_events``);
* релевантные факты из долгосрочной памяти.

Всё — только чтение. Возвращаем компактный текстовый блок (он уйдёт в user-роль
вместе с конкретным заданием). Любой сбой отдельного блока не валит весь
контекст — деградируем по частям.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.money import money
from app.features.drun import memory as drun_memory
from app.features.drun.names import name_for, resolve_names
from app.models import User, WorldEvent

logger = get_logger(__name__)


async def _player_block(session: AsyncSession, user_id: int) -> str:
    """Статистика игрока: баланс, MMR, репутация, дуэли, сообщения."""
    try:
        from app.repositories import mmr as mmr_repo
        from app.repositories import reputation as rep_repo

        user = await session.get(User, user_id)
        if user is None:
            return ""
        rep = await rep_repo.get_summary(session, user_id)
        # ReputationSummary.score = плюсы − минусы (нет поля total).
        rep_total = getattr(rep, "score", None)
        name = user.display_name() if user else str(user_id)
        lines = [
            f"Игрок: {name} (id={user_id})",
            f"- Баланс: {money(user.balance)}",
            f"- Всего заработано: {money(getattr(user, 'total_earned', 0))}",
            f"- MMR: {getattr(user, 'mmr', 0)}",
            f"- Репутация: {rep_total if rep_total is not None else 0}",
            f"- Дуэли: {getattr(user, 'duels_won', 0)} побед / "
            f"{getattr(user, 'duels_lost', 0)} поражений",
            f"- Сообщений: {getattr(user, 'messages_count', 0)}",
        ]
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("player_block failed", exc_info=True)
        return ""


async def _season_block(session: AsyncSession) -> str:
    try:
        from app.repositories import season as season_repo

        season = await season_repo.get_active_season(session)
        if season is None:
            return "Сезон: сейчас межсезонье."
        name = getattr(season, "name", None) or f"#{season.id}"
        return f"Сезон: идёт «{name}» (id={season.id})."
    except Exception:  # noqa: BLE001
        logger.debug("season_block failed", exc_info=True)
        return ""


async def _events_block(session: AsyncSession, limit: int = 12) -> str:
    """Последние события мира из world_events (для «что происходит»).

    Участники резолвятся в ники одним запросом (без сырых id в контексте).
    """
    try:
        rows = (
            await session.execute(
                select(WorldEvent)
                .order_by(WorldEvent.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        if not rows:
            return "События: пока тихо, мир спит."
        names = await resolve_names(
            session, [e.actor_id for e in rows] + [e.target_id for e in rows]
        )
        lines = ["Последние события мира:"]
        for ev in rows:
            amount = f" ({money(ev.amount)})" if ev.amount else ""
            who = f" {name_for(names, ev.actor_id)}" if ev.actor_id else ""
            tgt = f" → {name_for(names, ev.target_id)}" if ev.target_id else ""
            lines.append(f"- [{ev.type}]{who}{tgt}{amount}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("events_block failed", exc_info=True)
        return ""


async def _memory_block(session: AsyncSession, subject_id: int | None) -> str:
    try:
        mems = await drun_memory.relevant_memories(
            session, subject_id=subject_id, limit=8
        )
        if not mems:
            return ""
        lines = ["Что ты помнишь:"]
        for m in mems:
            lines.append(f"- {m.fact}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("memory_block failed", exc_info=True)
        return ""


async def _chat_block(session: AsyncSession, channel: str, limit: int = 14) -> str:
    """Свежая болтовня игроков в чате (кто что сказал) — по никам.

    Берём последние сообщения из ``ai_messages`` роли ``chat`` (реплики живых
    игроков, которые пишет middleware) и показываем как мини-стенограмму, чтобы
    друн отвечал ПО КОНТЕКСТУ беседы, а не в вакууме.
    """
    try:
        msgs = await drun_memory.recent_chat(session, channel=channel, limit=limit)
        if not msgs:
            return ""
        names = await resolve_names(session, [m.user_id for m in msgs])
        lines = ["Недавно в чате говорили:"]
        for m in msgs:
            who = (m.meta or {}).get("name") or name_for(names, m.user_id)
            lines.append(f"- {who}: {m.content}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("chat_block failed", exc_info=True)
        return ""


async def build_context(
    session: AsyncSession,
    *,
    subject_id: int | None = None,
    include_events: bool = True,
    channel: str = "chat",
    include_chat: bool = True,
) -> str:
    """Собирает полный контекстный блок (всё, что друн «видит» сейчас)."""
    blocks: list[str] = []
    if subject_id is not None:
        blocks.append(await _player_block(session, subject_id))
    blocks.append(await _season_block(session))
    if include_events:
        blocks.append(await _events_block(session))
    if include_chat:
        blocks.append(await _chat_block(session, channel))
    blocks.append(await _memory_block(session, subject_id))
    return "\n\n".join(b for b in blocks if b).strip()
