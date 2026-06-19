"""Эпизоды отношений — друн помнит, что люди СДЕЛАЛИ, а не только кто они.

Мнения (`opinions.py`) до сих пор лепились в основном из АГРЕГАТНОЙ статистики
(винрейт, репутация, серии). Этого мало для живого члена сообщества: люди
запоминаются не средними по больнице, а КОНКРЕТНЫМИ моментами — предал,
заступился, дал обещание и кинул, унизил, бросил вызов, помирился, расщедрился.

Этот модуль вводит ТИПИЗИРОВАННЫЕ социальные эпизоды. Каждый эпизод:

* привязан к игроку (``subject_id``) — «что ОН сделал» (по отношению к другому
  игроку, к чату или к самому друну);
* имеет ТИП из таксономии (betrayal/support/promise/...);
* несёт ``significance`` 1..3 — насколько момент памятен (живёт дольше);
* напрямую и ЗАМЕТНО двигает вектор мнения (в обход медленного EMA — см.
  ``opinions.apply_deltas``): после предательства друн думает о человеке иначе
  СРАЗУ, а не «постепенно по статистике».

Хранилище — существующая ``ai_memories`` (без миграции): ``kind='episode:<type>'``,
``source='episode'``, ``weight=significance``, ``expires_at`` зависит от
значимости (яркие — почти вечные). Так эпизоды бесплатно участвуют в ретривале,
скоринге и подмешивании в досье — друн ССЫЛАЕТСЯ на конкретный момент.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.models import AiMemory

logger = get_logger(__name__)

KIND_PREFIX = "episode:"
SOURCE = "episode"


@dataclass(frozen=True)
class EpisodeType:
    """Описание одного типа социального эпизода."""

    code: str               # машинный код (betrayal/support/...)
    label: str              # человекочитаемое (для досье/логов)
    deltas: dict[str, float]  # как ОДИН такой эпизод двигает вектор мнения
    base_significance: int  # 1..3 — насколько по умолчанию памятен
    valence: int            # +1 позитив / -1 негатив / 0 нейтрально-острый


# Таксономия (как в задании). deltas подобраны так, чтобы один яркий эпизод
# заметно (но не до предела) сдвигал соответствующие оси.
_TYPES: dict[str, EpisodeType] = {
    "betrayal": EpisodeType(
        "betrayal", "предательство",
        {"trust": -28, "respect": -10, "annoyance": +14}, 3, -1,
    ),
    "broken_promise": EpisodeType(
        "broken_promise", "слитое обещание",
        {"trust": -20, "reliability": -18, "annoyance": +8}, 2, -1,
    ),
    "promise": EpisodeType(
        "promise", "обещание",
        {"reliability": +4, "interest": +4}, 1, 0,
    ),
    "kept_promise": EpisodeType(
        "kept_promise", "сдержал слово",
        {"trust": +18, "reliability": +20, "respect": +8}, 2, +1,
    ),
    "support": EpisodeType(
        "support", "поддержка",
        {"trust": +14, "respect": +6, "annoyance": -8, "entertainment": +4}, 2, +1,
    ),
    "defense": EpisodeType(
        "defense", "заступился",
        {"trust": +18, "respect": +12, "annoyance": -6}, 2, +1,
    ),
    "generosity": EpisodeType(
        "generosity", "щедрость",
        {"trust": +12, "respect": +10, "entertainment": +6}, 2, +1,
    ),
    "leadership": EpisodeType(
        "leadership", "лидерство",
        {"respect": +20, "interest": +10, "trust": +6}, 2, +1,
    ),
    "humiliation": EpisodeType(
        "humiliation", "унижение",
        {"respect": -18, "annoyance": +16, "entertainment": +10, "chaos": +8}, 3, -1,
    ),
    "challenge": EpisodeType(
        "challenge", "вызов",
        {"respect": +8, "interest": +14, "chaos": +10}, 1, 0,
    ),
    "rivalry_escalation": EpisodeType(
        "rivalry_escalation", "эскалация вражды",
        {"interest": +16, "chaos": +14, "annoyance": +8, "respect": +4}, 2, 0,
    ),
    "reconciliation": EpisodeType(
        "reconciliation", "примирение",
        {"trust": +14, "annoyance": -16, "respect": +6}, 2, +1,
    ),
    "whining": EpisodeType(
        "whining", "нытьё",
        {"annoyance": +12, "respect": -6, "entertainment": -4}, 1, -1,
    ),
}

ALL_TYPES = tuple(_TYPES.keys())

# TTL по значимости (дни). Яркие эпизоды (significance=3) живут почти вечно —
# друн помнит «что человек сделал» месяцами, в отличие от разговорного шума
# (chat-факты живут 7 дней). Значимость 1 — обычная социальная мелочь.
_TTL_DAYS_BY_SIG = {1: 21, 2: 90, 3: 400}


def episode_type(code: str) -> EpisodeType | None:
    return _TYPES.get((code or "").strip().lower())


def kind_for(code: str) -> str:
    return f"{KIND_PREFIX}{code}"


def code_from_kind(kind: str) -> str:
    k = kind or ""
    return k[len(KIND_PREFIX):] if k.startswith(KIND_PREFIX) else k


def deltas_for(code: str, *, significance: int = 1) -> dict[str, float]:
    """Сдвиг вектора мнения от эпизода типа ``code`` с учётом значимости.

    Значимость масштабирует базовые дельты (значимый эпизод бьёт сильнее, но
    с насыщением, чтобы один момент не выкручивал ось в край).
    """
    et = episode_type(code)
    if et is None:
        return {}
    scale = {1: 0.8, 2: 1.0, 3: 1.35}.get(max(1, min(3, significance)), 1.0)
    return {ax: round(d * scale, 2) for ax, d in et.deltas.items()}


@dataclass
class Episode:
    """Записанный социальный эпизод (для рендера в досье)."""

    code: str
    label: str
    gist: str
    significance: int
    valence: int
    age_days: float


async def record_episode(
    session: AsyncSession,
    *,
    subject_id: int,
    code: str,
    gist: str,
    significance: int | None = None,
    nudge_opinion: bool = True,
) -> AiMemory | None:
    """Сохраняет социальный эпизод и (опц.) ЗАМЕТНО двигает мнение об игроке.

    ``gist`` — короткая суть «что сделал» в одну фразу (друн будет ссылаться на
    неё дословно). Коммит — на вызывающем. Возвращает запись или None.

    Идемпотентность по содержанию: одинаковый gist того же типа про того же
    игрока не дублируем (повтор лишь освежает/усиливает существующий).
    """
    et = episode_type(code)
    g = (gist or "").strip()
    if et is None or not g or not subject_id:
        return None
    sig = max(1, min(3, significance if significance is not None else et.base_significance))
    kind = kind_for(et.code)
    ttl_days = _TTL_DAYS_BY_SIG.get(sig, 21)
    expires = now_utc() + timedelta(days=ttl_days)

    try:
        # Дедуп: тот же тип + тот же текст про того же игрока.
        existing = (
            await session.execute(
                select(AiMemory)
                .where(AiMemory.subject_id == subject_id)
                .where(AiMemory.kind == kind)
                .where(AiMemory.fact == g[:240])
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Повтор того же момента — освежаем срок и чуть усиливаем вес, но
            # мнение НЕ двигаем второй раз (иначе один пересказ копил бы эффект).
            existing.weight = min(3, int(existing.weight or 1) + 1)
            existing.expires_at = expires
            await session.flush()
            return existing

        mem = AiMemory(
            subject_id=subject_id,
            kind=kind,
            fact=g[:240],
            weight=sig,
            source=SOURCE,
            expires_at=expires,
        )
        session.add(mem)
        await session.flush()
    except Exception:  # noqa: BLE001
        logger.debug("record_episode store failed", exc_info=True)
        return None

    if nudge_opinion:
        try:
            from app.features.drun import opinions as drun_opinions

            await drun_opinions.nudge_opinion(
                session, subject_id, deltas_for(et.code, significance=sig)
            )
        except Exception:  # noqa: BLE001
            logger.debug("record_episode opinion nudge failed", exc_info=True)
    return mem


async def recent_episodes(
    session: AsyncSession, user_id: int, *, limit: int = 5
) -> list[Episode]:
    """Последние памятные эпизоды игрока (свежие/значимые первыми, не протухшие)."""
    now = now_utc()
    try:
        rows = (
            await session.execute(
                select(AiMemory)
                .where(AiMemory.subject_id == user_id)
                .where(AiMemory.kind.like(f"{KIND_PREFIX}%"))
                .where(
                    (AiMemory.expires_at.is_(None)) | (AiMemory.expires_at > now)
                )
                .order_by(
                    AiMemory.weight.desc(), AiMemory.created_at.desc()
                )
                .limit(limit)
            )
        ).scalars().all()
    except Exception:  # noqa: BLE001
        logger.debug("recent_episodes failed", exc_info=True)
        return []
    out: list[Episode] = []
    for m in rows:
        code = code_from_kind(m.kind)
        et = episode_type(code)
        created = m.created_at
        try:
            age = max(0.0, (now - created).total_seconds() / 86400.0) if created else 0.0
        except Exception:  # noqa: BLE001
            age = 0.0
        out.append(
            Episode(
                code=code,
                label=et.label if et else code,
                gist=m.fact,
                significance=int(m.weight or 1),
                valence=et.valence if et else 0,
                age_days=age,
            )
        )
    return out


def render_block(episodes: list[Episode]) -> str:
    """Рендер «что человек делал» для досье. Пусто, если эпизодов нет."""
    if not episodes:
        return ""
    lines = [
        "- ЧТО ОН ДЕЛАЛ (твоя личная память о КОНКРЕТНЫХ поступках — можешь "
        "припомнить дословно, если в тему; НЕ зачитывай списком):"
    ]
    for ep in episodes:
        mark = "＋" if ep.valence > 0 else ("－" if ep.valence < 0 else "•")
        when = (
            "сегодня" if ep.age_days < 1
            else f"{int(ep.age_days)}д назад" if ep.age_days < 60
            else "давно"
        )
        star = "!" * ep.significance
        lines.append(f"  {mark} [{ep.label}{star}, {when}] {ep.gist}")
    return "\n".join(lines)
