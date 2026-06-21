"""Ингест реального Telegram Desktop export в долгую память Друна.

Источник: `result.json` из Telegram Desktop (`Export chat history` -> JSON).
Сырые сообщения НЕ складываем в `ai_messages`: это краткосрочная память с
ретеншеном. Вместо этого вся история читается, сжимается в `ai_memories`:
детерминированные факты активности + LLM-дистилляция локальных мемов, черт,
отношений и эпизодов по хронологическим чанкам.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.features.drun import config as drun_config
from app.features.drun import provider as drun_provider
from app.models import AiMemory

logger = get_logger(__name__)

SOURCE = "telegram_export"
_USER_RE = re.compile(r"^user(\d+)$")
_SPACE_RE = re.compile(r"\s+")
_MAX_FACT = 260


@dataclass(frozen=True)
class ExportMessage:
    message_id: int
    user_id: int | None
    name: str
    text: str
    dt: datetime | None
    reply_to_message_id: int | None = None


@dataclass(frozen=True)
class MemoryProposal:
    subject_id: int | None
    kind: str
    fact: str
    weight: int
    source: str = SOURCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "kind": self.kind,
            "fact": self.fact,
            "weight": self.weight,
            "source": self.source,
        }


def _clean(text: object, limit: int = 500) -> str:
    return _SPACE_RE.sub(" ", str(text or "").strip())[:limit]


def _parse_user_id(raw: object) -> int | None:
    m = _USER_RE.match(str(raw or ""))
    return int(m.group(1)) if m else None


def normalize_text(value: object) -> str:
    """Telegram export text can be str or mixed entity list."""
    if isinstance(value, str):
        return _clean(value, 2000)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return _clean("".join(parts), 2000)
    return ""


def _parse_dt(raw: object) -> datetime | None:
    if raw in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def load_export_messages(path: str | Path) -> list[ExportMessage]:
    """Loads text messages from Telegram Desktop JSON export."""
    with Path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    out: list[ExportMessage] = []
    for item in data.get("messages") or []:
        if item.get("type") != "message":
            continue
        text = normalize_text(item.get("text"))
        if not text:
            continue
        out.append(ExportMessage(
            message_id=int(item.get("id") or 0),
            user_id=_parse_user_id(item.get("from_id")),
            name=_clean(item.get("from") or "", 80),
            text=text,
            dt=_parse_dt(item.get("date_unixtime")),
            reply_to_message_id=(
                int(item["reply_to_message_id"])
                if item.get("reply_to_message_id") is not None else None
            ),
        ))
    return out


def filter_messages(
    messages: list[ExportMessage], *, exclude_user_ids: set[int] | None = None
) -> list[ExportMessage]:
    """Filters export messages before learning.

    By default the script excludes the bot/Drun account: learning from its own
    exported replies creates a feedback loop and teaches it to imitate itself
    instead of the chat.
    """
    exclude_user_ids = exclude_user_ids or set()
    if not exclude_user_ids:
        return messages
    return [m for m in messages if m.user_id not in exclude_user_ids]


def _chunks(messages: list[ExportMessage], size: int) -> Iterable[list[ExportMessage]]:
    for i in range(0, len(messages), size):
        chunk = messages[i : i + size]
        if chunk:
            yield chunk


def selected_chunks(
    messages: list[ExportMessage], *, chunk_size: int, max_chunks: int | None
) -> list[list[ExportMessage]]:
    """Returns chunks for LLM distill, sampling evenly across full history.

    Processing every 90-message chunk of a 100k+ export would be thousands of LLM
    calls. If max_chunks is set, we spread the budget across the whole timeline
    instead of taking only the beginning.
    """
    chunks = list(_chunks(messages, max(1, chunk_size)))
    if max_chunks is None or max_chunks <= 0 or len(chunks) <= max_chunks:
        return chunks
    if max_chunks == 1:
        return [chunks[len(chunks) // 2]]
    last = len(chunks) - 1
    idxs = sorted({round(i * last / (max_chunks - 1)) for i in range(max_chunks)})
    return [chunks[i] for i in idxs]


def chunk_range(
    messages: list[ExportMessage], *, chunk_size: int, start: int = 0, count: int | None = None
) -> list[list[ExportMessage]]:
    """Returns a contiguous 0-based chunk range for resumable batch learning."""
    chunks = list(_chunks(messages, max(1, chunk_size)))
    start = max(0, int(start or 0))
    if count is None or count <= 0:
        return chunks[start:]
    return chunks[start : start + count]


def build_deterministic_proposals(messages: list[ExportMessage]) -> list[MemoryProposal]:
    """Cheap full-history facts without LLM calls."""
    by_user: dict[int, list[ExportMessage]] = defaultdict(list)
    names: dict[int, Counter[str]] = defaultdict(Counter)
    phrases: Counter[str] = Counter()
    for m in messages:
        if m.user_id is not None:
            by_user[m.user_id].append(m)
            if m.name:
                names[m.user_id][m.name] += 1
        low = m.text.lower().strip()
        if 2 <= len(low) <= 80 and not low.startswith("/"):
            phrases[low] += 1

    out: list[MemoryProposal] = []
    if messages:
        first = min((m.dt for m in messages if m.dt), default=None)
        last = max((m.dt for m in messages if m.dt), default=None)
        if first and last:
            out.append(MemoryProposal(
                None,
                "legend",
                (
                    f"Реальный Telegram-export чата охватывает {len(messages)} "
                    f"текстовых сообщений с {first.date()} по {last.date()}. "
                    "Это источник старой живой речи, мемов и конфликтов Возни."
                ),
                3,
            ))

    for uid, rows in sorted(by_user.items(), key=lambda kv: len(kv[1]), reverse=True)[:120]:
        if len(rows) < 20:
            continue
        name = names[uid].most_common(1)[0][0] if names[uid] else f"id{uid}"
        first = min((m.dt for m in rows if m.dt), default=None)
        last = max((m.dt for m in rows if m.dt), default=None)
        date_part = f" с {first.date()} по {last.date()}" if first and last else ""
        weight = 3 if len(rows) >= 1000 else 2 if len(rows) >= 200 else 1
        out.append(MemoryProposal(
            uid,
            "trait",
            f"{name} написал(а) {len(rows)} сообщений в реальном Telegram-export{date_part}; это важный голос старой истории чата.",
            weight,
        ))
        for alias, count in names[uid].most_common(4):
            if alias and alias != name and count >= 3:
                out.append(MemoryProposal(
                    uid,
                    "chat:nickname",
                    f"В Telegram-export {name} также появлялся(ась) под именем «{alias}».",
                    1,
                ))

    noisy = {"да", "нет", "ок", "ага", "ахах", "хах", "лол", "пон", "че", "что"}
    for phrase, count in phrases.most_common(80):
        if count < 5 or phrase in noisy or len(phrase) < 4:
            continue
        out.append(MemoryProposal(
            None,
            "chat:meme",
            f"В старом Telegram-export фраза/мем «{phrase[:80]}» повторялась примерно {count} раз.",
            2 if count >= 15 else 1,
        ))
    return out


_DISTILL_SYSTEM = (
    "Ты — архивариус живого Telegram-чата. По фрагменту старой истории выдели "
    "только устойчивые социальные факты: локальные мемы, клички, черты людей, "
    "конфликты, дружбу, яркие поступки, стиль речи. Не пересказывай обычную "
    "болтовню и не выдумывай."
)
_DISTILL_INSTRUCTION = (
    "Верни СТРОГО JSON-массив до 8 объектов: "
    '{"user_id":123 или null,"kind":"trait|topic|nickname|meme|relationship|episode|legend",'
    '"fact":"короткий факт по-русски","weight":1-3}. '
    "user_id ставь только если факт явно про одного человека из лога. "
    "kind episode — только для яркого поступка/конфликта/поддержки. "
    "Если ничего устойчивого нет — верни []."
)


def _parse_llm_items(raw: str) -> list[MemoryProposal]:
    text = (raw or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[MemoryProposal] = []
    kind_map = {
        "trait": "chat:trait",
        "topic": "chat:topic",
        "nickname": "chat:nickname",
        "meme": "chat:meme",
        "relationship": "chat:relationship",
        "episode": "episode:export",
        "legend": "legend",
    }
    for item in data[:8]:
        if not isinstance(item, dict):
            continue
        fact = _clean(item.get("fact"), _MAX_FACT)
        if len(fact) < 8:
            continue
        uid = item.get("user_id")
        try:
            subject_id = int(uid) if uid not in (None, "", "null") else None
        except (TypeError, ValueError):
            subject_id = None
        kind = kind_map.get(str(item.get("kind") or "").lower(), "chat")
        try:
            weight = max(1, min(3, int(item.get("weight") or 1)))
        except (TypeError, ValueError):
            weight = 1
        out.append(MemoryProposal(subject_id, kind, fact, weight))
    return out


async def distill_export_chunks(
    session: AsyncSession,
    messages: list[ExportMessage],
    *,
    chunk_size: int = 90,
    max_chunks: int | None = 120,
    start_chunk: int | None = None,
) -> list[MemoryProposal]:
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return []
    out: list[MemoryProposal] = []
    if start_chunk is not None:
        chunks = chunk_range(
            messages,
            chunk_size=chunk_size,
            start=start_chunk,
            count=max_chunks,
        )
    else:
        chunks = selected_chunks(messages, chunk_size=chunk_size, max_chunks=max_chunks)
    for idx, chunk in enumerate(chunks, start=1):
        lines = []
        for m in chunk:
            who = f"{m.name or 'unknown'} [user_id={m.user_id}]"
            lines.append(f"{who}: {m.text[:500]}")
        try:
            raw = await drun_provider.chat(
                cfg,
                system=_DISTILL_SYSTEM,
                messages=[{"role": "user", "content": f"{_DISTILL_INSTRUCTION}\n\n# ЛОГ\n" + "\n".join(lines)}],
                model=cfg.model_for(drun_config.ROLE_MEMORY_EXTRACT),
            )
        except drun_provider.LlmError as exc:
            logger.warning("telegram export distill chunk %s failed: %s", idx, exc)
            continue
        out.extend(_parse_llm_items(raw))
    return out


async def apply_proposals(
    session: AsyncSession,
    proposals: list[MemoryProposal],
    *,
    dry_run: bool = True,
) -> dict[str, int]:
    stats = {"seen": len(proposals), "inserted": 0, "skipped": 0}
    existing = set((await session.execute(
        select(AiMemory.subject_id, AiMemory.kind, AiMemory.fact)
        .where(AiMemory.source == SOURCE)
    )).all())
    for p in proposals:
        key = (p.subject_id, p.kind, p.fact)
        if key in existing:
            stats["skipped"] += 1
            continue
        existing.add(key)
        if not dry_run:
            session.add(AiMemory(
                subject_id=p.subject_id,
                kind=p.kind,
                fact=p.fact,
                weight=max(1, min(3, int(p.weight))),
                source=p.source,
            ))
        stats["inserted"] += 1
    return stats


# --- Мост: имена из export → AiProfile.data["aliases"] -----------------------
#
# ВАЖНО: trusted-резолв owner-команд (``aliases.resolve_alias``) ищет прозвища
# ТОЛЬКО в ``AiProfile.data["aliases"]``. Дистилляция export'а кладёт клички в
# ``ai_memories`` (kind='chat:nickname') как ТЕКСТ — этот путь резолв не видит.
# Поэтому имена, под которыми человек реально фигурировал в истории, надо
# положить прямо в профиль, чтобы «друн дай пете 500» нашёл Петю из импорта.
#
# Преимущество export'а: user_id известен ТОЧНО (из ``from_id``), без разрешения
# тёзок по логу окна — мис-привязка к чужому человеку исключена.

# Сколько раз имя должно встретиться в истории, чтобы считать его значимым
# (отсекаем разовые опечатки/смену ника на один день).
_ALIAS_MIN_COUNT = 2
# Потолок веса, который один импорт добавляет прозвищу. Намеренно НЕ даём
# дотянуть до автономного порога резолва (``_MIN_RESOLVE_WEIGHT``=3) с одного
# импорта: исторические display-имена безопасны для owner-команд (вес ≥1), но
# не должны сами по себе разрешать АВТОНОМНЫЕ действия без подтверждения чатом.
_ALIAS_MAX_IMPORT_WEIGHT = 2


def collect_profile_aliases(
    messages: list[ExportMessage],
) -> dict[int, list[str]]:
    """Имена, под которыми каждый user_id фигурировал в истории (для профиля).

    Чистая функция. Возвращает ``{user_id: [имя, ...]}`` — имена, встреченные
    не реже ``_ALIAS_MIN_COUNT`` раз, по убыванию частоты. Список построен так,
    что частые имена повторяются (до ``_ALIAS_MAX_IMPORT_WEIGHT`` раз), чтобы
    ``aliases.add_aliases`` накопил им соответствующий вес за один проход.
    """
    names: dict[int, Counter[str]] = defaultdict(Counter)
    for m in messages:
        if m.user_id is not None and m.name:
            names[m.user_id][m.name] += 1

    out: dict[int, list[str]] = {}
    for uid, counter in names.items():
        ordered: list[str] = []
        for name, count in counter.most_common():
            if count < _ALIAS_MIN_COUNT:
                continue
            # Вес = частота, но не выше потолка импорта (см. константу).
            ordered.extend([name] * min(count, _ALIAS_MAX_IMPORT_WEIGHT))
        if ordered:
            out[uid] = ordered
    return out


async def apply_profile_aliases(
    session: AsyncSession,
    alias_map: dict[int, list[str]],
    *,
    dry_run: bool = True,
) -> dict[str, int]:
    """Дописывает имена из истории в ``AiProfile.data['aliases']``.

    Создаёт минимальный профиль, если его ещё нет (свип позже обогатит его
    портретом). Накопление веса и анти-раздувание — внутри ``add_aliases``.
    Commit — на вызывающем. Возвращает счётчики для отчёта.
    """
    from app.features.drun import aliases as drun_aliases
    from app.models import AiProfile

    stats = {"users": len(alias_map), "profiles_touched": 0, "created": 0}
    for uid, new_aliases in alias_map.items():
        if not new_aliases:
            continue
        prof = await session.get(AiProfile, uid)
        merged = drun_aliases.add_aliases(
            (prof.data or {}).get("aliases") if prof is not None else None,
            new_aliases,
        )
        stats["profiles_touched"] += 1
        if dry_run:
            continue
        if prof is None:
            session.add(AiProfile(user_id=uid, data={"aliases": merged}))
            stats["created"] += 1
        else:
            data = dict(prof.data or {})
            data["aliases"] = merged
            prof.data = data
    return stats

