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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.money import money
from app.features.drun import attitude as drun_attitude
from app.features.drun import memory as drun_memory
from app.features.drun.names import name_for, resolve_names
from app.models import User, WorldEvent

logger = get_logger(__name__)


async def _player_block(session: AsyncSession, user_id: int) -> str:
    """Досье игрока: статистика + брак + ОТНОШЕНИЕ Друна к нему.

    Это самый важный блок: он делает ответ персональным. Кроме сухих цифр сюда
    идёт «стойка» (stance) — как Друну держаться именно с этим человеком.
    """
    try:
        from app.repositories import marriages as marr_repo
        from app.repositories import reputation as rep_repo

        user = await session.get(User, user_id)
        if user is None:
            return ""
        rep = await rep_repo.get_summary(session, user_id)
        rep_score = getattr(rep, "score", 0) or 0
        rep_plus = getattr(rep, "plus", 0) or 0
        rep_minus = getattr(rep, "minus", 0) or 0
        name = user.display_name()

        lines = [
            f"# ДОСЬЕ НА СОБЕСЕДНИКА: {name} (id={user_id})",
            f"- Баланс: {money(user.balance)}, всего заработано: "
            f"{money(getattr(user, 'total_earned', 0))}",
            f"- MMR: {getattr(user, 'mmr', 0)}, дуэли: "
            f"{getattr(user, 'duels_won', 0)}W/{getattr(user, 'duels_lost', 0)}L",
            f"- Репутация в чате: {rep_score:+d} (плюсов {rep_plus}, минусов {rep_minus})",
            f"- Сообщений в чате: {getattr(user, 'messages_count', 0)}",
        ]

        # Брак — повод для подколов/контекста отношений.
        try:
            marriage = await marr_repo.get_active_marriage(session, user_id)
            if marriage is not None:
                partner_id = (
                    marriage.user_id_2
                    if marriage.user_id_1 == user_id
                    else marriage.user_id_1
                )
                pnames = await resolve_names(session, [partner_id])
                lines.append(f"- В браке с {name_for(pnames, partner_id)}")
        except Exception:  # noqa: BLE001
            logger.debug("marriage lookup failed", exc_info=True)

        # Стойка Друна к этому игроку — ключ к персональности.
        stance = await drun_attitude.get_stance(session, user_id)
        if stance is not None:
            lines.append(
                f"- ТВОЁ ОТНОШЕНИЕ [{stance.label}]: {stance.directive}"
            )

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


async def _events_block(session: AsyncSession, limit: int = 6) -> str:
    """Краткая сводка последних событий мира — ФОН, не главный материал.

    Намеренно компактно (6 строк): события — это приправа, а не суть разговора.
    Друн не должен в каждой реплике пересказывать ленту дуэлей.
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
            return ""
        names = await resolve_names(
            session, [e.actor_id for e in rows] + [e.target_id for e in rows]
        )
        lines = ["Фоном в мире (можешь упомянуть, если в тему, но не пересказывай):"]
        for ev in rows:
            amount = f" ({money(ev.amount)})" if ev.amount else ""
            who = f" {name_for(names, ev.actor_id)}" if ev.actor_id else ""
            tgt = f" → {name_for(names, ev.target_id)}" if ev.target_id else ""
            lines.append(f"- [{ev.type}]{who}{tgt}{amount}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("events_block failed", exc_info=True)
        return ""


async def _overview_block(session: AsyncSession) -> str:
    """Общая картина чата: топы, богачи, бойцы, семьи, активные болтуны.

    Это «что вообще происходит у нас» — широкий срез базы, чтобы друн владел
    обстановкой и мог переключаться с темы на тему, а не упирался в дуэли.
    """
    try:
        from sqlalchemy import func

        lines: list[str] = ["# ОБЩАЯ КАРТИНА ЧАТА (для эрудиции, не пересказывай списком):"]

        # Сколько народу всего и сколько активных болтунов.
        total_users = await session.scalar(select(func.count()).select_from(User))
        if total_users:
            lines.append(f"- Всего жителей: {total_users}")

        # Топ-3 богача по балансу.
        rich = (
            await session.execute(
                select(User.user_id, User.balance)
                .order_by(User.balance.desc())
                .limit(3)
            )
        ).all()
        if rich:
            rnames = await resolve_names(session, [r[0] for r in rich])
            top = ", ".join(
                f"{name_for(rnames, uid)} ({money(bal)})" for uid, bal in rich
            )
            lines.append(f"- Богачи по ешкам: {top}")

        # Топ-3 болтуна (самые активные в чате).
        chatty = (
            await session.execute(
                select(User.user_id, User.messages_count)
                .order_by(User.messages_count.desc())
                .limit(3)
            )
        ).all()
        if chatty:
            cnames = await resolve_names(session, [r[0] for r in chatty])
            top = ", ".join(
                f"{name_for(cnames, uid)} ({cnt} сообщ.)" for uid, cnt in chatty
            )
            lines.append(f"- Самые болтливые: {top}")

        # Сколько семей в чате.
        try:
            from app.repositories import marriages as marr_repo

            married = await marr_repo.get_married_user_ids(session)
            if married:
                lines.append(f"- В браках состоит: {len(married)} чел.")
        except Exception:  # noqa: BLE001
            logger.debug("overview marriages failed", exc_info=True)

        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception:  # noqa: BLE001
        logger.debug("overview_block failed", exc_info=True)
        return ""


async def _memory_block(session: AsyncSession, subject_id: int | None) -> str:
    try:
        mems = await drun_memory.relevant_memories(
            session, subject_id=subject_id, limit=16
        )
        if not mems:
            return ""
        lines = ["Что ты помнишь про людей и мир (используй для подколов и связей):"]
        for m in mems:
            lines.append(f"- {m.fact}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("memory_block failed", exc_info=True)
        return ""


async def _chat_block(session: AsyncSession, channel: str, limit: int = 24) -> str:
    """Свежая болтовня игроков в чате (кто что сказал) — по никам.

    ГЛАВНЫЙ материал для ответа: о чём реально говорят люди прямо сейчас. Берём
    широкое окно (24 реплики), чтобы Друн чувствовал беседу, а не одну фразу.
    """
    try:
        msgs = await drun_memory.recent_chat(session, channel=channel, limit=limit)
        if not msgs:
            return ""
        names = await resolve_names(session, [m.user_id for m in msgs])
        lines = [
            "# ЖИВОЙ ЧАТ ПРЯМО СЕЙЧАС (снизу — самые свежие реплики).",
            "# Прочитай и пойми НАСТРОЕНИЕ и О ЧЁМ базар, прежде чем встревать:",
        ]
        for m in msgs:
            who = (m.meta or {}).get("name") or name_for(names, m.user_id)
            lines.append(f"{who}: {m.content}")
        lines.append("# (последняя строка выше — самое свежее в чате)")
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
    """Собирает полный контекстный блок (всё, что друн «видит» сейчас).

    Порядок = приоритет внимания модели: сначала ДОСЬЕ на собеседника и ЖИВОЙ
    ЧАТ, потом ПАМЯТЬ про людей, и лишь в конце — фон (сезон, события).
    """
    blocks: list[str] = []
    if subject_id is not None:
        blocks.append(await _player_block(session, subject_id))
    if include_chat:
        blocks.append(await _chat_block(session, channel))
    blocks.append(await _memory_block(session, subject_id))
    blocks.append(await _overview_block(session))
    blocks.append(await _season_block(session))
    if include_events:
        blocks.append(await _events_block(session))
    return "\n\n".join(b for b in blocks if b).strip()
