"""Read-tools: инструменты ПРОВЕРКИ реальности перед действием.

Агенту мало «думать» — перед тем как ляпнуть факт или принять решение, друн
должен иметь возможность СВЕРИТЬСЯ с актуальной БД, а не полагаться на снимок
контекста (который мог устареть или быть неполным). Это read-only слой: никаких
мутаций, только быстрые точечные запросы.

В отличие от ``tools.py`` (действия владельца) и ``registry.py`` (диспетчеризация
write-команд), здесь — ЧТЕНИЕ для верификации: баланс игрока, топы, история
движений, отношения. Дёшево, безопасно, можно звать на горячем пути перед
автономным вкидом, чтобы факт в реплике был ПРАВДОЙ, а не галлюцинацией.
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


@dataclass(frozen=True)
class FactCheck:
    """Результат проверки факта: правда ли + человекочитаемое пояснение."""

    ok: bool
    detail: str


async def get_balance(session: AsyncSession, user_id: int) -> int | None:
    """Актуальный баланс игрока (None — игрока нет)."""
    try:
        return await session.scalar(
            select(User.balance).where(User.id == user_id)
        )
    except Exception:  # noqa: BLE001
        logger.debug("get_balance failed", exc_info=True)
        return None


async def get_top(
    session: AsyncSession, *, by: str = "balance", limit: int = 5
) -> list[tuple[int, int]]:
    """Топ игроков по полю (balance/mmr/messages_count). Список (user_id, value)."""
    col = {
        "balance": User.balance,
        "mmr": User.mmr,
        "messages_count": User.messages_count,
        "season_mmr": User.season_mmr,
    }.get(by, User.balance)
    try:
        rows = (
            await session.execute(
                select(User.id, col).order_by(col.desc()).limit(limit)
            )
        ).all()
        return [(int(uid), int(v or 0)) for uid, v in rows]
    except Exception:  # noqa: BLE001
        logger.debug("get_top failed", exc_info=True)
        return []


async def get_rank(
    session: AsyncSession, user_id: int, *, by: str = "balance"
) -> int | None:
    """Место игрока в рейтинге по полю (1 = первый). None — не найден."""
    col = {
        "balance": User.balance, "mmr": User.mmr,
        "messages_count": User.messages_count, "season_mmr": User.season_mmr,
    }.get(by, User.balance)
    try:
        my = await session.scalar(select(col).where(User.id == user_id))
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
    """Нетто движений игрока по источникам за окно (reason → net)."""
    try:
        since = now_utc() - timedelta(days=days)
        rows = (
            await session.execute(
                select(Transaction.reason, func.sum(Transaction.amount))
                .where(Transaction.user_id == user_id)
                .where(Transaction.created_at >= since)
                .group_by(Transaction.reason)
            )
        ).all()
        return {r: int(net or 0) for r, net in rows}
    except Exception:  # noqa: BLE001
        logger.debug("get_money_history failed", exc_info=True)
        return {}


async def verify_claim(
    session: AsyncSession, *, user_id: int, claim: str
) -> FactCheck:
    """Грубая проверка типового утверждения о деньгах игрока.

    Поддерживает простые проверки, которые друн часто хочет сделать перед
    подъёбом: «он богатейший», «он на нуле», «он всё спустил в казино».
    Не пытается понимать произвольный текст — это дешёвый детерминированный
    щит от галлюцинаций на самых частых заявлениях.
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
    return FactCheck(True, f"баланс {money(bal)} (утверждение не проверяемо точно)")


async def describe_top(
    session: AsyncSession, *, by: str = "balance", limit: int = 5
) -> str:
    """Топ игроков строкой с именами (для прямой сверки/реплики)."""
    rows = await get_top(session, by=by, limit=limit)
    if not rows:
        return ""
    names = await resolve_names(session, [uid for uid, _ in rows])
    fmt = money if by in ("balance",) else (lambda v: str(v))
    return ", ".join(f"{name_for(names, uid)} ({fmt(v)})" for uid, v in rows)
