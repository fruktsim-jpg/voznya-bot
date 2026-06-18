"""Read-директивы друна: сверка фактов перед финальной репликой.

Друн в ЧЕРНОВИКЕ ответа может попросить факты из БД, вставив директиву на
отдельной строке::

    [[ask:top:balance]]            — топ по богатству (метрика опциональна)
    [[ask:rank:@vasya:balance]]    — место игрока в рейтинге
    [[ask:player:@vasya]]          — досье на любого игрока (третье лицо!)
    [[ask:balance:@vasya]]         — только баланс
    [[ask:economy:@vasya]]         — откуда деньги за неделю
    [[ask:inventory:@vasya]]       — что в инвентаре
    [[ask:relations:@vasya]]       — брак/кореша/соперники

Зачем директивы, а не tool-loop провайдера: gateway отдаёт только текст (нет
нативного function-calling), и он флакёт (см. INCIDENT 2026-06-18 part 4 —
пустые тела). Директива даёт РОВНО ОДИН доп. вызов и только если черновик её
содержит; обычные реплики идут как раньше, без накладных расходов.

Параллель с ``actions.py`` (econ-директивы) намеренная: тот же синтаксис
``[[...]]``, та же дисциплина «распарсить → выполнить → вырезать из видимого
текста». Здесь — ТОЛЬКО ЧТЕНИЕ, без мутаций и без предохранителей сумм.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.features.drun import tools_read

logger = get_logger(__name__)

# [[ask:verb:arg1:arg2]] — арги опциональны. Регистронезависимо.
# verb ∈ {top, rank, player, balance, economy, inventory, relations}.
_ASK_RE = re.compile(
    r"\[\[\s*ask\s*:\s*([a-z_]+)\s*(?::\s*([^:\]]*))?\s*(?::\s*([^\]]*))?\]\]",
    re.IGNORECASE,
)

# Сколько директив максимум исполняем за один ответ (анти-абуз/анти-латенси).
_MAX_DIRECTIVES = 4


@dataclass(frozen=True)
class AskDirective:
    """Распарсенная read-директива (до исполнения)."""

    verb: str          # top / rank / player / balance / economy / inventory / relations
    arg1: str          # обычно метрика (top/rank) или кто (остальное)
    arg2: str          # метрика для rank, иначе пусто


def strip_directives(text: str) -> str:
    """Убирает все [[ask:...]] из видимого текста и схлопывает пустоты."""
    cleaned = _ASK_RE.sub("", text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def has_directive(text: str) -> bool:
    """Быстрая проверка наличия хотя бы одной директивы (без полного парса)."""
    return bool(_ASK_RE.search(text or ""))


def parse_all(text: str) -> list[AskDirective]:
    """Достаёт все корректные директивы из текста (до _MAX_DIRECTIVES)."""
    out: list[AskDirective] = []
    for m in _ASK_RE.finditer(text or ""):
        verb = (m.group(1) or "").lower().strip()
        arg1 = (m.group(2) or "").strip()
        arg2 = (m.group(3) or "").strip()
        if verb:
            out.append(AskDirective(verb=verb, arg1=arg1, arg2=arg2))
        if len(out) >= _MAX_DIRECTIVES:
            break
    return out


# Метрики, которые умеет ранжировать tools_read (для валидации arg).
_VALID_METRICS = {"balance", "mmr", "season_mmr", "messages", "duels_won", "pidor"}


async def _resolve_one(session: AsyncSession, d: AskDirective) -> str:
    """Исполняет одну директиву, возвращает строку-факт (или пусто при сбое)."""
    verb = d.verb
    if verb == "top":
        by = d.arg1 if d.arg1 in _VALID_METRICS else "balance"
        return await tools_read.describe_top(session, by=by, limit=5)

    # Остальные глаголы адресуют конкретного игрока в arg1.
    who = d.arg1
    if not who:
        return ""
    uid = await tools_read.resolve_who(session, who)
    if uid is None:
        return f"({who}: игрок не найден в базе)"

    if verb == "rank":
        by = d.arg2 if d.arg2 in _VALID_METRICS else "balance"
        rank = await tools_read.get_rank(session, uid, by=by)
        if rank is None:
            return f"({who}: нет в рейтинге)"
        return f"{who}: место #{rank} по «{by}»"
    if verb == "balance":
        bal = await tools_read.get_balance(session, uid)
        from app.core.money import money

        return f"{who}: баланс {money(bal or 0)}"
    if verb == "player":
        return await tools_read.describe_player(session, uid)
    if verb == "economy":
        return await tools_read.describe_economy(session, uid)
    if verb == "inventory":
        return await tools_read.describe_inventory(session, uid)
    if verb == "relations":
        return await tools_read.describe_relations(session, uid)
    return ""


async def resolve(session: AsyncSession, text: str) -> str:
    """Исполняет все директивы из черновика, возвращает блок фактов для модели.

    Пусто, если директив нет или ни одна не дала результата. Каждый резолв в
    своём savepoint: сбой одной директивы не роняет остальные и не отравляет
    общую транзакцию (INCIDENT 2026-06-18).
    """
    directives = parse_all(text)
    if not directives:
        return ""
    facts: list[str] = []
    for d in directives:
        try:
            async with session.begin_nested():
                fact = await _resolve_one(session, d)
        except Exception:  # noqa: BLE001
            logger.warning("ask directive failed: %s", d.verb, exc_info=True)
            fact = ""
        if fact:
            facts.append(f"- {fact}")
    if not facts:
        return ""
    return "# СВЕРКА С БАЗОЙ (это ПРАВДА, опирайся на эти цифры):\n" + "\n".join(facts)
