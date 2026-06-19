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
# Сколько раз пара должна оказаться «рядом», чтобы это считалось сигналом
# (сырой минимум доказательств, до нормировки на болтливость).
_FRIEND_RAW_MIN = 4
# Итоговый порог «кореша» уже по НОРМИРОВАННОМУ баллу (см. _buddy_counts).
_FRIEND_MIN = 3
# Максимальный разрыв во времени между соседними репликами, чтобы считать их
# одной беседой (сек). Реплики в часах друг от друга — это не диалог, а просто
# два человека, писавшие в один день; без этого окна болтуны «дружат» со всеми.
_ADJ_WINDOW_SEC = 180

# Приоритет вида связи при схлопывании нескольких рёбер одного человека в одно.
# Брак/вражда/соперничество — более «определяющие» отношения, чем кореш/даритель,
# поэтому при конфликте побеждает более сильный по смыслу вид.
_KIND_PRIORITY: dict[str, int] = {
    "spouse": 5,
    "rival": 4,
    "foe": 3,
    "ally": 2,
    "gifter": 1,
    "buddy": 0,
}


def _edge_rank(edge: "RelEdge") -> tuple[int, int]:
    """Ключ выбора «лучшего» ребра для человека: (приоритет вида, сила)."""
    return (_KIND_PRIORITY.get(edge.kind, 0), edge.strength)


def _score_buddies(
    seq: list[tuple[int, float]], user_id: int
) -> Counter[int]:
    """Чистая нормировка «корешей» из последовательности (автор, время_сек).

    ``seq`` — реплики чата по возрастанию времени, ``ts`` в epoch-секундах.
    Возвращает ``other_id → нормированный балл близости`` (целое, читаемое как
    условные «реплики диалога»). Логика и мотивация — в :func:`_buddy_counts`.
    """
    total: Counter[int] = Counter(uid for uid, _ in seq)
    raw: Counter[int] = Counter()
    n = len(seq)
    for i, (uid, ts) in enumerate(seq):
        if uid != user_id:
            continue
        # соседи в пределах ±2 реплик И ближе _ADJ_WINDOW_SEC по времени
        for j in range(max(0, i - 2), min(n, i + 3)):
            if j == i:
                continue
            other, ots = seq[j]
            if not other or other == user_id:
                continue
            if abs(ots - ts) <= _ADJ_WINDOW_SEC:
                raw[other] += 1
    # Нормируем: балл = со-появления / корень из общей активности собеседника.
    # Корень смягчает штраф (активный, но реально близкий человек не обнуляется),
    # но гасит глобальных болтунов, которые мелькают рядом со всеми.
    cnt: Counter[int] = Counter()
    for other, rc in raw.items():
        if rc < _FRIEND_RAW_MIN:
            continue
        background = max(1, total.get(other, 1))
        score = rc / (background ** 0.5)
        scaled = int(round(score * (background ** 0.25)))
        if scaled > 0:
            cnt[other] = scaled
    return cnt


def _dedupe_by_person(
    edges: "list[RelEdge] | dict", max_edges: int
) -> "list[RelEdge]":
    """Схлопывает рёбра по человеку и возвращает топ из РАЗНЫХ людей.

    Один и тот же сосед мог попасть в несколько видов (buddy + gifter + ally);
    без схлопывания один доминирующий человек занимал бы все слоты топа, а в
    досье его имя дублировалось. Оставляем по каждому человеку ОДНО, самое
    осмысленное ребро (по :func:`_edge_rank`).
    """
    values = edges.values() if isinstance(edges, dict) else edges
    best_per_person: dict[int, RelEdge] = {}
    for edge in values:
        prev = best_per_person.get(edge.other_id)
        if prev is None or _edge_rank(edge) > _edge_rank(prev):
            best_per_person[edge.other_id] = edge
    ranked = sorted(
        best_per_person.values(), key=_edge_rank, reverse=True
    )
    return ranked[:max_edges]


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
    """Кореша: кто реально ведёт ДИАЛОГ с игроком (а не просто болтлив).

    Сырая эвристика «соседних реплик» ломалась в живом чате: самые активные
    болтуны оказывались «рядом» со всеми и становились корешами каждого. Чинит
    это две поправки:

    * ВРЕМЕННОЕ ОКНО: реплики считаются соседними, только если идут в пределах
      ``_ADJ_WINDOW_SEC`` друг от друга. Сообщения с разрывом в часы — это не
      диалог, а просто активность в один день.
    * НОРМИРОВКА НА БОЛТЛИВОСТЬ: сырой счётчик со-появлений делим на «фоновую»
      активность собеседника (сколько он вообще пишет). Так общительный сосед,
      который реально переписывается именно с игроком, обгоняет глобального
      болтуна, который мелькает рядом со всеми по инерции.

    Вся арифметика вынесена в чистую :func:`_score_buddies` (тестируется без БД);
    здесь только выборка истории чата.
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
    seq: list[tuple[int, float]] = []
    for uid, ts in rows:
        try:
            seq.append((uid, ts.timestamp()))
        except Exception:  # noqa: BLE001
            seq.append((uid, 0.0))
    return _score_buddies(seq, user_id)


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
                    edges[key] = RelEdge(oid, "", "buddy", strength=c)
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

    # Схлопываем рёбра по ЧЕЛОВЕКУ: один и тот же сосед мог попасть в несколько
    # видов (buddy + gifter + ally), и без этого один доминирующий человек
    # занимал бы все слоты топа, а в досье его имя дублировалось. Оставляем по
    # каждому человеку ОДНО, самое осмысленное ребро (см. _dedupe_by_person),
    # чтобы итоговый список был из РАЗНЫХ людей.
    return _dedupe_by_person(edges, max_edges)
