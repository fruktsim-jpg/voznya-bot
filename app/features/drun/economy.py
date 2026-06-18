"""Экономическое чутьё друна — он ПОНИМАЕТ потоки ешек, а не только балансы.

Друн — дух экономики Возни. Чтобы вести себя как хозяин казны, он должен видеть
не статичный баланс, а ДВИЖЕНИЕ денег: кто фармит, кто сливает в казино, кто
кому дарит, откуда приходят и куда утекают ешки в чате. Эти данные он раньше не
видел вовсе (контекст знал лишь баланс + total_earned).

Два уровня:
* ``chat_economy_digest`` — макро-картина чата за окно: эмиссия (faucets) против
  стока (sinks), инфляция, самые активные источники/ямы. Дорого (агрегаты), но
  кэшируется на минуту — зовётся раз в overview.
* ``player_money_digest`` — микро-картина одного игрока: его свежие движения по
  reason'ам (нафармил / просадил в казино / выиграл дуэли) и денежные связи
  (с кем дуэлит, кому дарит) — материал для личных подколов и решений.

Всё — чистые читатели БД (read-only), без записи. Семантика reason'ов берётся из
``transactions.reason`` (farm/casino/duel/treasure/purchase/reward/...).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.money import money
from app.core.utils import now_utc
from app.models import Transaction, User

logger = get_logger(__name__)

# Человекочитаемые ярлыки источников движения ешек (reason → русское слово).
_REASON_RU: dict[str, str] = {
    "farm": "ферма", "casino": "казино", "duel": "дуэли", "treasure": "клады",
    "purchase": "покупки", "reward": "награды", "achievement": "ачивки",
    "mission": "миссии", "nomination": "номинации", "daily": "ежедневки",
    "admin": "админ", "owner_drun": "друн", "drun_tax": "налог друна",
    "drun_grant": "подачка друна", "gift": "подарки", "marriage": "брак",
}

# Reason'ы, которые НЕ являются «честной игрой»: эмиссия сверху (админ/друн).
# Друну полезно отделять заработанное игроками от напечатанного.
_FAUCET_OVERRIDE = {"admin", "owner_drun", "drun_grant"}


def _ru(reason: str) -> str:
    return _REASON_RU.get(reason, reason)


@dataclass
class ChatEconomy:
    """Макро-снимок экономики чата за окно."""

    minted: int = 0          # сколько ешек влилось (сумма положительных)
    burned: int = 0          # сколько сгорело (сумма отрицательных, по модулю)
    total_balance: int = 0   # сколько ешек на руках сейчас
    flows: list[tuple[str, int, int]] = field(default_factory=list)
    # flows: (reason, net, abs_volume) — нетто и оборот по источнику

    @property
    def net(self) -> int:
        return self.minted - self.burned


async def chat_economy_digest(
    session: AsyncSession, *, hours: int = 24
) -> str:
    """Макро-картина: куда движутся ешки в чате за последние ``hours`` часов.

    Группируем транзакции по reason: нетто (+приток/−сток) и оборот. Так друн
    видит реальную динамику — «казино высосало X, фарм влил Y» — и может
    осмысленно комментировать инфляцию, жадность казны, активность игроков.
    """
    try:
        since = now_utc() - timedelta(hours=hours)
        rows = (
            await session.execute(
                select(
                    Transaction.reason,
                    func.sum(Transaction.amount),
                    func.sum(func.abs(Transaction.amount)),
                    func.count(),
                )
                .where(Transaction.created_at >= since)
                .group_by(Transaction.reason)
            )
        ).all()
        if not rows:
            return ""

        total_balance = int(
            await session.scalar(select(func.coalesce(func.sum(User.balance), 0)))
            or 0
        )
        minted = sum(int(net) for _, net, _, _ in rows if net and net > 0)
        burned = -sum(int(net) for _, net, _, _ in rows if net and net < 0)
        # Топ источников по обороту — самые «живые» части экономики.
        flows = sorted(
            ((r, int(net or 0), int(vol or 0)) for r, net, vol, _ in rows),
            key=lambda x: x[2],
            reverse=True,
        )

        lines = [
            f"# ЭКОНОМИКА ЧАТА (за {hours}ч) — ты дух этой казны, понимай потоки:",
            f"- В обороте сейчас: {money(total_balance)} на руках у игроков",
            f"- Влилось: +{money(minted)} | сгорело: −{money(burned)} | "
            f"итог: {money(minted - burned)}",
        ]
        flow_bits = []
        for reason, net, vol in flows[:6]:
            sign = "+" if net >= 0 else "−"
            flow_bits.append(f"{_ru(reason)} {sign}{money(abs(net))}")
        if flow_bits:
            lines.append("- Потоки по источникам: " + ", ".join(flow_bits))
        # Подсказка-интерпретация для модели (чтобы не зачитывал цифры, а понял).
        biggest_sink = min(flows, key=lambda x: x[1]) if flows else None
        biggest_src = max(flows, key=lambda x: x[1]) if flows else None
        hint = []
        if biggest_sink and biggest_sink[1] < 0:
            hint.append(f"больше всего сжирает {_ru(biggest_sink[0])}")
        if biggest_src and biggest_src[1] > 0:
            hint.append(f"кормит чат {_ru(biggest_src[0])}")
        if hint:
            lines.append("- Суть: " + "; ".join(hint) + " (используй для подъёбов/реакций, без зачитки цифр)")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("chat_economy_digest failed", exc_info=True)
        return ""


async def player_money_digest(
    session: AsyncSession, user_id: int, *, days: int = 7
) -> str:
    """Микро-картина денег игрока: свежие движения по источникам за окно.

    Друн видит, ЧЕМ игрок живёт: нафармил столько-то, просадил в казино,
    поднял на дуэлях. Это топливо для личных, точных реакций («опять в казино
    всё спустил?») вместо общих фраз.
    """
    try:
        since = now_utc() - timedelta(days=days)
        rows = (
            await session.execute(
                select(
                    Transaction.reason,
                    func.sum(Transaction.amount),
                    func.count(),
                )
                .where(Transaction.user_id == user_id)
                .where(Transaction.created_at >= since)
                .group_by(Transaction.reason)
            )
        ).all()
        if not rows:
            return ""
        parts = []
        for reason, net, cnt in sorted(
            rows, key=lambda x: abs(int(x[1] or 0)), reverse=True
        )[:5]:
            net = int(net or 0)
            sign = "+" if net >= 0 else "−"
            parts.append(f"{_ru(reason)} {sign}{money(abs(net))} ({cnt})")
        if not parts:
            return ""
        return f"- Деньги за {days}д: " + ", ".join(parts)
    except Exception:  # noqa: BLE001
        logger.debug("player_money_digest failed", exc_info=True)
        return ""


async def player_relations_digest(
    session: AsyncSession, user_id: int, *, days: int = 14, limit: int = 4
) -> str:
    """С кем игрок взаимодействует: дуэли и подарки (соц-граф через события).

    Через ``world_events`` (actor↔target) видим, кто чей соперник/благодетель.
    Друн понимает социальные связи («вы с Петей вечно деретесь», «опять Маше
    задарил») — это уже не сухая экономика, а отношения между людьми.
    Имена резолвит вызывающий слой контекста; тут возвращаем сырые id-пары,
    чтобы не тянуть resolve_names внутрь экономики.
    """
    try:
        from app.models import WorldEvent

        since = now_utc() - timedelta(days=days)
        rows = (
            await session.execute(
                select(WorldEvent.type, WorldEvent.actor_id, WorldEvent.target_id)
                .where(WorldEvent.created_at >= since)
                .where(
                    (WorldEvent.actor_id == user_id)
                    | (WorldEvent.target_id == user_id)
                )
                .where(WorldEvent.target_id.is_not(None))
                .order_by(WorldEvent.created_at.desc())
                .limit(60)
            )
        ).all()
        if not rows:
            return ""
        # Считаем интенсивность связи с каждым контрагентом.
        from collections import Counter

        counter: Counter[int] = Counter()
        for _typ, actor, target in rows:
            other = target if actor == user_id else actor
            if other and other != user_id:
                counter[other] += 1
        if not counter:
            return ""
        top = counter.most_common(limit)
        return "RELATIONS:" + ",".join(f"{uid}:{n}" for uid, n in top)
    except Exception:  # noqa: BLE001
        logger.debug("player_relations_digest failed", exc_info=True)
        return ""
