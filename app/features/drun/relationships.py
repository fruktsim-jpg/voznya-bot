"""Граф отношений между игроками для друна.

Друн должен знать не только КТО человек, но и С КЕМ он связан: кто кореш, кто
заклятый соперник, на ком женат, кого постоянно поминает в чате. Это делает
реплики живыми («ты ж с X вечно грызётесь», «спроси у жены»).

Связи считаем детерминированно из базы (без LLM):
* брак — ``marriages`` (партнёр);
* соперничество — частые дуэли между парой (``world_events`` duel_won);
* симпатия/вражда — кто кому ставил репу (``reputation_entries``);
* кореша — частые со-упоминания/диалоги в чате (``ai_messages``).

Возвращаем по игроку список рёбер ``RelEdge`` (с кем, тип, насколько сильно),
которые кладутся в профиль и подмешиваются в досье. Любой сбой блока — молча.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun.names import name_for, resolve_names
from app.models import AiMessage, WorldEvent

logger = get_logger(__name__)

# Порог «соперничества»: столько дуэлей между парой.
_RIVALRY_MIN = 3
# Окно со-упоминаний в чате (дни) и порог «корешей».
_COMENTION_DAYS = 14
_FRIEND_MIN = 4


@dataclass
class RelEdge:
    """Ребро графа: связь игрока с другим человеком."""

    other_id: int
    other_name: str
    kind: str          # spouse / rival / ally / foe / buddy / gifter
    strength: int = 1
    note: str = ""


async def spouse_of(session: AsyncSession, user_id: int) -> int | None:
    """ID партнёра по активному браку (или None). Единый источник правила пары.

    Все места, где нужен супруг (досье, профиль, граф связей), должны ходить
    сюда, чтобы конвенция ``user_id_1``/``user_id_2`` жила в одном месте и не
    разъезжалась между поверхностями при изменении модели брака.
    """
    try:
        from app.repositories import marriages as marr_repo

        marriage = await marr_repo.get_active_marriage(session, user_id)
        if marriage is None:
            return None
        return (
            marriage.user_id_2
            if marriage.user_id_1 == user_id
            else marriage.user_id_1
        )
    except Exception:  # noqa: BLE001
        logger.debug("spouse_of failed", exc_info=True)
        return None


async def _spouse_edge(session: AsyncSession, user_id: int) -> tuple[int, str] | None:
    """Партнёр по активному браку (id, имя) или None."""
    pid = await spouse_of(session, user_id)
    if pid is None:
        return None
    names = await resolve_names(session, [pid])
    return pid, name_for(names, pid)


async def _rival_counts(session: AsyncSession, user_id: int) -> Counter[int]:
    """Сколько дуэлей у игрока с каждым соперником (по world_events)."""
    rows = (
        await session.execute(
            select(WorldEvent.actor_id, WorldEvent.target_id)
            .where(WorldEvent.type == "duel_won")
            .where(
                (WorldEvent.actor_id == user_id)
                | (WorldEvent.target_id == user_id)
            )
            .order_by(WorldEvent.created_at.desc())
            .limit(300)
        )
    ).all()
    cnt: Counter[int] = Counter()
    for actor, target in rows:
        other = target if actor == user_id else actor
        if other and other != user_id:
            cnt[other] += 1
    return cnt


async def _rep_edges(
    session: AsyncSession, user_id: int
) -> tuple[Counter[int], Counter[int]]:
    """Кто ставил игроку плюсы (ally) и минусы (foe), с весами."""
    from app.models import ReputationEntry

    rows = (
        await session.execute(
            select(ReputationEntry.giver_user_id, ReputationEntry.value)
            .where(ReputationEntry.target_user_id == user_id)
        )
    ).all()
    allies: Counter[int] = Counter()
    foes: Counter[int] = Counter()
    for giver, value in rows:
        if not giver:
            continue
        if value > 0:
            allies[giver] += 1
        elif value < 0:
            foes[giver] += 1
    return allies, foes


async def _buddy_counts(session: AsyncSession, user_id: int) -> Counter[int]:
    """Кореша: кто часто пишет в чате «рядом» с игроком (соседние реплики).

    Грубая эвристика дружбы: берём окно последних реплик чата и считаем, чьи
    сообщения идут вплотную к сообщениям игрока (диалог идёт между ними).
    """
    since = now_utc() - timedelta(days=_COMENTION_DAYS)
    rows = (
        await session.execute(
            select(AiMessage.user_id, AiMessage.created_at)
            .where(AiMessage.role == "chat")
            .where(AiMessage.user_id.is_not(None))
            .where(AiMessage.created_at >= since)
            .order_by(AiMessage.created_at.asc())
            .limit(4000)
        )
    ).all()
    seq = [uid for uid, _ in rows]
    cnt: Counter[int] = Counter()
    for i, uid in enumerate(seq):
        if uid != user_id:
            continue
        # соседи в пределах ±2 реплик — вероятный диалог
        for j in range(max(0, i - 2), min(len(seq), i + 3)):
            other = seq[j]
            if other and other != user_id:
                cnt[other] += 1
    return cnt


async def _gift_counts(session: AsyncSession, user_id: int) -> Counter[int]:
    """Кто кому дарит (#4): счётчик подарочных связей игрока с другими.

    Считаем подарки игрок→игрок в обе стороны (``gift_transactions`` с
    ``gift_type='player'``). Положительный счётчик = устойчивая дарительная связь.
    """
    from app.models import GiftTransaction

    rows = (
        await session.execute(
            select(
                GiftTransaction.sender_user_id,
                GiftTransaction.recipient_user_id,
            )
            .where(GiftTransaction.gift_type == "player")
            .where(
                (GiftTransaction.sender_user_id == user_id)
                | (GiftTransaction.recipient_user_id == user_id)
            )
            .order_by(GiftTransaction.created_at.desc())
            .limit(300)
        )
    ).all()
    cnt: Counter[int] = Counter()
    for sender, recipient in rows:
        other = recipient if sender == user_id else sender
        if other and other != user_id:
            cnt[other] += 1
    return cnt


# Порог дарительной связи: столько подарков между парой.
_GIFT_MIN = 2


async def compute_edges(
    session: AsyncSession, user_id: int, *, max_edges: int = 6
) -> list[RelEdge]:
    """Считает связи игрока со всеми другими и возвращает топ по силе."""
    edges: dict[tuple[int, str], RelEdge] = {}
    need_names: set[int] = set()

    # Брак — самая сильная связь.
    spouse = await _spouse_edge(session, user_id)
    if spouse is not None:
        pid, pname = spouse
        edges[(pid, "spouse")] = RelEdge(pid, pname, "spouse", strength=10)

    # Соперничества по дуэлям.
    try:
        rivals = await _rival_counts(session, user_id)
        for oid, c in rivals.items():
            if c >= _RIVALRY_MIN:
                edges[(oid, "rival")] = RelEdge(oid, "", "rival", strength=c)
                need_names.add(oid)
    except Exception:  # noqa: BLE001
        logger.debug("rival edges failed", exc_info=True)

    # Репутация: союзники/недоброжелатели.
    try:
        allies, foes = await _rep_edges(session, user_id)
        for oid, c in allies.items():
            edges[(oid, "ally")] = RelEdge(oid, "", "ally", strength=c)
            need_names.add(oid)
        for oid, c in foes.items():
            edges[(oid, "foe")] = RelEdge(oid, "", "foe", strength=c + 1)
            need_names.add(oid)
    except Exception:  # noqa: BLE001
        logger.debug("rep edges failed", exc_info=True)

    # Кореша по чату.
    try:
        buddies = await _buddy_counts(session, user_id)
        for oid, c in buddies.most_common(8):
            if c >= _FRIEND_MIN and (oid, "spouse") not in edges:
                key = (oid, "buddy")
                if key not in edges:
                    edges[key] = RelEdge(oid, "", "buddy", strength=c // 2)
                    need_names.add(oid)
    except Exception:  # noqa: BLE001
        logger.debug("buddy edges failed", exc_info=True)

    # Подарочные связи (#4): кто кому дарит.
    try:
        gifts = await _gift_counts(session, user_id)
        for oid, c in gifts.items():
            if c >= _GIFT_MIN and (oid, "spouse") not in edges:
                key = (oid, "gifter")
                if key not in edges:
                    edges[key] = RelEdge(oid, "", "gifter", strength=c)
                    need_names.add(oid)
    except Exception:  # noqa: BLE001
        logger.debug("gift edges failed", exc_info=True)

    if need_names:
        names = await resolve_names(session, need_names)
        for (oid, _), edge in edges.items():
            if not edge.other_name:
                edge.other_name = name_for(names, oid)

    ranked = sorted(edges.values(), key=lambda e: e.strength, reverse=True)
    return ranked[:max_edges]
