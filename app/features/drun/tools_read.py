"""Read-tools: проверка реальности перед тем, как друн ляпнет факт.

Друн уже видит ДОСЬЕ собеседника (context._player_block: баланс, MMR, дуэли,
репутация, инвентарь, брак, ачивки, модерация). Но он СЛЕП к двум вещам:
третьи лица («кто богаче, я или Вася?») и лидерборды («я топ-1?»). Именно там
он галлюцинирует. Этот модуль — точечные read-only запросы к БД для сверки.

Все функции опираются на канонические репозитории (``app/repositories/*``) и
ключ ``users.user_id`` (НЕ ``id``: у модели User PK — ``user_id``). Любой сбой
деградирует к None/пустому — верификация не должна ронять ответ.

Слой ЧТЕНИЯ; в отличие от ``tools.py`` (мутации владельца) и ``registry.py``
(диспетчер write-команд) — здесь только SELECT'ы, безопасные на горячем пути.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.money import money
from app.core.utils import now_utc
from app.features.drun.names import name_for, resolve_names
from app.models import Transaction, User

logger = get_logger(__name__)

# Разрешённые метрики топа/ранга → колонка User. Только денормализованные поля
# на самой строке игрока (дёшево, по индексу). season_mmr/mmr/balance уже
# проиндексированы (см. models/user.py).
_RANKABLE = {
    "balance": User.balance,
    "mmr": User.mmr,
    "season_mmr": User.season_mmr,
    "messages": User.messages_count,
    "duels_won": User.duels_won,
    "pidor": User.pidor_count,
}

# Человекочитаемые ярлыки метрик (для строк-ответов друну).
_METRIC_LABEL = {
    "balance": "баланс",
    "mmr": "MMR",
    "season_mmr": "сезонный MMR",
    "messages": "сообщений",
    "duels_won": "побед в дуэлях",
    "pidor": "раз пидор дня",
}


def _fmt_metric(by: str, value: int) -> str:
    """Формат значения метрики: баланс — деньгами, остальное — числом."""
    return money(value) if by == "balance" else str(value)


@dataclass(frozen=True)
class FactCheck:
    """Результат проверки факта: правда ли + человекочитаемое пояснение."""

    ok: bool
    detail: str


async def resolve_who(session: AsyncSession, who: str) -> int | None:
    """Резолвит строку (@username / id / имя / кличка) в user_id.

    Переиспользует единый резолвер из tools.py, чтобы поиск игрока вёл себя
    одинаково в write- и read-инструментах (включая выученные клички).
    """
    try:
        from app.features.drun.tools import find_user_id

        return await find_user_id(session, who)
    except Exception:  # noqa: BLE001
        logger.debug("resolve_who failed for %r", who, exc_info=True)
        return None


async def get_balance(session: AsyncSession, user_id: int) -> int | None:
    """Актуальный баланс игрока (None — игрока нет)."""
    try:
        return await session.scalar(
            select(User.balance).where(User.user_id == user_id)
        )
    except Exception:  # noqa: BLE001
        logger.debug("get_balance failed", exc_info=True)
        return None


async def get_top(
    session: AsyncSession, *, by: str = "balance", limit: int = 5
) -> list[tuple[int, int]]:
    """Топ игроков по метрике. Список (user_id, value), по убыванию."""
    col = _RANKABLE.get(by, User.balance)
    limit = max(1, min(int(limit or 5), 15))
    try:
        rows = (
            await session.execute(
                select(User.user_id, col)
                .where(col > 0)
                .order_by(col.desc())
                .limit(limit)
            )
        ).all()
        return [(int(uid), int(v or 0)) for uid, v in rows]
    except Exception:  # noqa: BLE001
        logger.debug("get_top failed", exc_info=True)
        return []


async def get_rank(
    session: AsyncSession, user_id: int, *, by: str = "balance"
) -> int | None:
    """Место игрока в рейтинге по метрике (1 = первый). None — не найден."""
    col = _RANKABLE.get(by, User.balance)
    try:
        my = await session.scalar(select(col).where(User.user_id == user_id))
        if my is None:
            return None
        higher = await session.scalar(
            select(func.count()).select_from(User).where(col > my)
        )
        return int(higher or 0) + 1
    except Exception:  # noqa: BLE001
        logger.debug("get_rank failed", exc_info=True)
        return None


async def get_money_history(
    session: AsyncSession, user_id: int, *, days: int = 7
) -> dict[str, int]:
    """Нетто движений игрока по источникам за окно (reason → net ешек)."""
    try:
        since = now_utc() - timedelta(days=max(1, min(int(days or 7), 90)))
        rows = (
            await session.execute(
                select(Transaction.reason, func.sum(Transaction.amount))
                .where(Transaction.user_id == user_id)
                .where(Transaction.created_at >= since)
                .group_by(Transaction.reason)
                .order_by(func.sum(Transaction.amount))
            )
        ).all()
        return {r: int(net or 0) for r, net in rows}
    except Exception:  # noqa: BLE001
        logger.debug("get_money_history failed", exc_info=True)
        return {}


async def describe_top(
    session: AsyncSession, *, by: str = "balance", limit: int = 5
) -> str:
    """Топ игроков одной строкой с именами (для прямой сверки/реплики)."""
    rows = await get_top(session, by=by, limit=limit)
    if not rows:
        return ""
    names = await resolve_names(session, [uid for uid, _ in rows])
    parts = [
        f"{i}. {name_for(names, uid)} — {_fmt_metric(by, v)}"
        for i, (uid, v) in enumerate(rows, 1)
    ]
    label = _METRIC_LABEL.get(by, by)
    return f"Топ по «{label}»: " + "; ".join(parts)


async def describe_player(session: AsyncSession, user_id: int) -> str:
    """Сжатое досье на ЛЮБОГО игрока одной строкой — для сверки про третьих лиц.

    Это и есть закрытие главной дыры: про собеседника досье уже есть в контексте,
    а про упомянутого третьего человека («а правда Вася богаче меня?») друн до
    сих пор был слеп. Берём дешёвые денормализованные поля + баланс/ранг.
    """
    try:
        user = await session.get(User, user_id)
    except Exception:  # noqa: BLE001
        logger.debug("describe_player get failed", exc_info=True)
        user = None
    if user is None:
        return ""
    names = await resolve_names(session, [user_id])
    nm = name_for(names, user_id)
    bal = int(getattr(user, "balance", 0) or 0)
    rank = await get_rank(session, user_id, by="balance")
    bits = [f"{nm}: баланс {money(bal)}"]
    if rank is not None:
        bits.append(f"#{rank} по богатству")
    mmr = int(getattr(user, "mmr", 0) or 0)
    if mmr:
        bits.append(f"MMR {mmr}")
    dw = int(getattr(user, "duels_won", 0) or 0)
    dl = int(getattr(user, "duels_lost", 0) or 0)
    if dw or dl:
        bits.append(f"дуэли {dw}W/{dl}L")
    msgs = int(getattr(user, "messages_count", 0) or 0)
    if msgs:
        bits.append(f"сообщений {msgs}")
    pidor = int(getattr(user, "pidor_count", 0) or 0)
    if pidor:
        bits.append(f"пидор дня ×{pidor}")
    return ", ".join(bits)


async def describe_relations(session: AsyncSession, user_id: int) -> str:
    """Брак + граф отношений игрока одной строкой (кореша/соперники/супруг)."""
    out: list[str] = []
    try:
        from app.repositories import marriages as m_repo

        marr = await m_repo.get_active_marriage(session, user_id)
        if marr is not None:
            partner = (
                marr.user_id_2 if marr.user_id_1 == user_id else marr.user_id_1
            )
            pnames = await resolve_names(session, [partner])
            out.append(f"в браке с {name_for(pnames, partner)}")
    except Exception:  # noqa: BLE001
        logger.debug("describe_relations marriage failed", exc_info=True)
    try:
        from app.features.drun import relationships as rel_mod

        edges = await rel_mod.compute_edges(session, user_id, max_edges=4)
        ids = [e.other_id for e in edges if getattr(e, "other_id", None)]
        enames = await resolve_names(session, ids)
        for e in edges:
            oid = getattr(e, "other_id", None)
            kind = getattr(e, "kind", "") or ""
            if oid:
                out.append(f"{kind}: {name_for(enames, oid)}")
    except Exception:  # noqa: BLE001
        logger.debug("describe_relations edges failed", exc_info=True)
    return "; ".join(out)


async def describe_inventory(session: AsyncSession, user_id: int) -> str:
    """Инвентарь игрока: сколько предметов + топ-несколько по редкости."""
    try:
        from app.repositories import inventory as inv_repo

        total = await inv_repo.count_items(session, user_id)
        if not total:
            return "инвентарь пуст"
        distinct = await inv_repo.count_distinct_items(session, user_id)
        rows = await inv_repo.get_inventory(session, user_id, limit=5)
        items = ", ".join(
            f"{r.name}×{r.quantity}" if r.quantity > 1 else r.name for r in rows
        )
        return f"{total} предметов ({distinct} видов): {items}"
    except Exception:  # noqa: BLE001
        logger.debug("describe_inventory failed", exc_info=True)
        return ""


async def describe_economy(session: AsyncSession, user_id: int, *, days: int = 7) -> str:
    """Откуда у игрока деньги за окно: топ источников притока/оттока."""
    hist = await get_money_history(session, user_id, days=days)
    if not hist:
        return "движений нет"
    gains = sorted(((v, r) for r, v in hist.items() if v > 0), reverse=True)[:3]
    losses = sorted((v, r) for r, v in hist.items() if v < 0)[:3]
    parts: list[str] = []
    if gains:
        parts.append(
            "приток: " + ", ".join(f"{r} {money(v)}" for v, r in gains)
        )
    if losses:
        parts.append(
            "отток: " + ", ".join(f"{r} {money(v)}" for v, r in losses)
        )
    return f"за {days}д — " + "; ".join(parts)


async def verify_claim(
    session: AsyncSession, *, user_id: int, claim: str
) -> FactCheck:
    """Дешёвый детерминированный щит от галлюцинаций на частых заявлениях.

    Понимает не произвольный текст, а самые ходовые подъёбы про деньги: «он
    богатейший», «он на нуле», «он всё спустил в казино». Для остального
    возвращает баланс и помечает, что точную проверку не сделать.
    """
    low = (claim or "").lower()
    bal = await get_balance(session, user_id)
    if bal is None:
        return FactCheck(False, "игрок не найден")
    if any(w in low for w in ("богат", "топ", "richest", "больше всех")):
        rank = await get_rank(session, user_id, by="balance")
        ok = rank is not None and rank <= 3
        return FactCheck(ok, f"баланс {money(bal)}, место #{rank} по богатству")
    if any(w in low for w in ("на нул", "бомж", "нищ", "пуст", "банкрот")):
        ok = bal < 100
        return FactCheck(ok, f"баланс {money(bal)}")
    if "казино" in low:
        hist = await get_money_history(session, user_id, days=7)
        casino = hist.get("casino", 0)
        ok = casino < 0
        return FactCheck(ok, f"казино за 7д: {money(casino)}")
    return FactCheck(True, f"баланс {money(bal)} (точно не проверяемо)")
