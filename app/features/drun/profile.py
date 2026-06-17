"""Профили игроков: сборка богатого досье из ВСЕЙ базы + LLM-портрет.

Идея: на каждого активного игрока друн держит собранный портрет (``ai_profiles``):
* сырая стата отовсюду (баланс, mmr, репутация, дуэли, брак, ачивки, кейсы,
  подарки, ферма, активность) — детерминированно, без LLM;
* LLM-саммари ЛИЧНОСТИ и МАНЕРЫ РЕЧИ — по последним репликам игрока в чате.

Профиль обновляется в реальном времени (вскоре после активности игрока, с
дебаунсом) и периодическим свипом. Подмешивается в контекст ответа — так друн
«знает» собеседника как живого человека, а не по сухим цифрам.

Любой сбой отдельного блока деградирует молча: профиль строится по тому, что
удалось собрать.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.models import AiMessage, AiProfile, User

logger = get_logger(__name__)

# Сколько последних реплик игрока отдаём модели на анализ личности/речи.
_MSG_WINDOW = 40
# Дебаунс реалтайма: не пересобирать профиль чаще, чем раз в N минут…
_REFRESH_DEBOUNCE_MIN = 12
# …кроме случая, когда накопилось достаточно новых реплик с прошлой сборки.
_REFRESH_MSG_DELTA = 15


@dataclass
class RawStats:
    """Сырые данные по игроку, собранные из разных таблиц (всё опционально)."""

    name: str = ""
    lines: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


async def gather_stats(session: AsyncSession, user_id: int) -> RawStats:
    """Собирает ВСЁ, что база знает про игрока. Деградирует по частям."""
    from app.core.money import money

    rs = RawStats()
    user = await session.get(User, user_id)
    if user is None:
        return rs
    rs.name = user.display_name()

    # --- База: профиль/экономика/игра -------------------------------------
    bal = getattr(user, "balance", 0) or 0
    earned = getattr(user, "total_earned", 0) or 0
    spent = getattr(user, "total_spent", 0) or 0
    mmr = getattr(user, "mmr", 0) or 0
    dw = getattr(user, "duels_won", 0) or 0
    dl = getattr(user, "duels_lost", 0) or 0
    msgs = getattr(user, "messages_count", 0) or 0
    rs.stats.update(
        balance=bal, total_earned=earned, total_spent=spent, mmr=mmr,
        duels_won=dw, duels_lost=dl, messages_count=msgs,
        treasures_found=getattr(user, "treasures_found", 0) or 0,
        casino_games=getattr(user, "casino_games_count", 0) or 0,
        farm_streak=getattr(user, "farm_streak", 0) or 0,
        pidor_count=getattr(user, "pidor_count", 0) or 0,
    )
    rs.lines.append(
        f"Баланс {money(bal)}, заработал за всё время {money(earned)}, "
        f"спустил {money(spent)}."
    )
    rs.lines.append(
        f"MMR {mmr}, дуэли {dw}W/{dl}L, сообщений в чате {msgs}, "
        f"кейсов/казино-игр {rs.stats['casino_games']}, "
        f"кладов найдено {rs.stats['treasures_found']}."
    )
    if rs.stats["pidor_count"]:
        rs.lines.append(f"Был «пидором дня» {rs.stats['pidor_count']} раз.")
    return rs


async def _augment_social(session: AsyncSession, user_id: int, rs: RawStats) -> None:
    """Добавляет в досье репутацию и брак (мягко, не ломая сборку)."""
    try:
        from app.repositories import reputation as rep_repo

        summ = await rep_repo.get_summary(session, user_id)
        rs.stats.update(rep=summ.score, rep_plus=summ.plus, rep_minus=summ.minus)
        rs.lines.append(
            f"Репутация {summ.score:+d} (плюсов {summ.plus}, минусов {summ.minus})."
        )
    except Exception:  # noqa: BLE001
        logger.debug("profile reputation failed", exc_info=True)

    try:
        from app.features.drun import relationships as rel_mod
        from app.features.drun.names import name_for, resolve_names

        partner_id = await rel_mod.spouse_of(session, user_id)
        if partner_id is not None:
            pnames = await resolve_names(session, [partner_id])
            partner = name_for(pnames, partner_id)
            rs.stats["married_to"] = partner
            rs.lines.append(f"В браке с {partner}.")
    except Exception:  # noqa: BLE001
        logger.debug("profile marriage failed", exc_info=True)


async def _player_messages(session: AsyncSession, user_id: int) -> list[str]:
    """Последние реплики самого игрока (для анализа личности и манеры речи)."""
    rows = (
        await session.execute(
            select(AiMessage.content)
            .where(AiMessage.role == "chat")
            .where(AiMessage.user_id == user_id)
            .order_by(AiMessage.created_at.desc())
            .limit(_MSG_WINDOW)
        )
    ).scalars().all()
    return [r for r in reversed(rows) if r]


_PORTRAIT_SYSTEM = (
    "Ты — психолог-портретист. По репликам человека из чата составь сжатый, но "
    "живой портрет: какой у него характер, чем он живёт, как себя ведёт, и "
    "ОТДЕЛЬНО — как он ПИШЕТ (манера речи: словечки, мат, капс, смайлы, длина "
    "реплик, грамотность, фишки). Без воды и морали — это рабочая заметка."
)
_PORTRAIT_INSTRUCTION = (
    "Верни СТРОГО JSON-объект (без пояснений, без ```):\n"
    '{{"summary":"1-3 фразы кто это и какой по характеру",'
    '"speech":"1-2 фразы как именно он пишет",'
    '"traits":["короткая черта", "ещё черта"],'
    '"topics":["о чём чаще говорит"]}}\n'
    "Пиши по-русски, живо, конкретно. Если реплик мало — заполни что можешь, "
    "остальное оставь пустым/[]."
)


async def _build_portrait(session: AsyncSession, name: str, msgs: list[str]) -> dict:
    """LLM-портрет личности и манеры речи по репликам игрока. {} при сбое."""
    import json

    from app.features.drun import config as drun_config
    from app.features.drun import provider as drun_provider

    if len(msgs) < 4:
        return {}
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return {}
    log = "\n".join(f"- {m}" for m in msgs[-_MSG_WINDOW:])
    user_msg = f"{_PORTRAIT_INSTRUCTION}\n\n# РЕПЛИКИ ИГРОКА {name}\n{log}"
    try:
        raw = await drun_provider.chat(
            cfg, system=_PORTRAIT_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            model=cfg.fast_model or None,
        )
    except drun_provider.LlmError as exc:
        logger.debug("portrait llm failed: %s", exc)
        return {}
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    summ = str(data.get("summary", "")).strip()
    speech = str(data.get("speech", "")).strip()
    if summ:
        out["summary"] = summ[:500]
    if speech:
        out["speech"] = speech[:300]
    traits = data.get("traits")
    if isinstance(traits, list):
        out["traits"] = [str(t).strip()[:80] for t in traits[:6] if str(t).strip()]
    topics = data.get("topics")
    if isinstance(topics, list):
        out["topics"] = [str(t).strip()[:60] for t in topics[:6] if str(t).strip()]
    return out


async def refresh_profile(
    session: AsyncSession, user_id: int, *, force: bool = False
) -> AiProfile | None:
    """Пересобирает профиль игрока из всей базы + LLM-портрет.

    ``force=False`` уважает дебаунс (не чаще раза в N минут и только если
    накопились новые реплики). Commit — на вызывающем.
    """
    prof = await session.get(AiProfile, user_id)
    msg_count = await session.scalar(
        select(func.count())
        .select_from(AiMessage)
        .where(AiMessage.role == "chat")
        .where(AiMessage.user_id == user_id)
    )
    msg_count = int(msg_count or 0)

    if not force and prof is not None and prof.refreshed_at is not None:
        fresh = now_utc() - prof.refreshed_at < timedelta(minutes=_REFRESH_DEBOUNCE_MIN)
        few_new = (msg_count - (prof.messages_seen or 0)) < _REFRESH_MSG_DELTA
        if fresh and few_new:
            return prof

    rs = await gather_stats(session, user_id)
    if not rs.name:
        return prof
    await _augment_social(session, user_id, rs)
    msgs = await _player_messages(session, user_id)
    portrait = await _build_portrait(session, rs.name, msgs)

    # Граф отношений: с кем связан игрок (брак/соперники/кореша/репа).
    edges_data: list[dict] = []
    try:
        from app.features.drun import relationships as rel_mod

        edges = await rel_mod.compute_edges(session, user_id)
        edges_data = [
            {
                "id": e.other_id, "name": e.other_name,
                "kind": e.kind, "strength": e.strength,
            }
            for e in edges
        ]
    except Exception:  # noqa: BLE001
        logger.debug("relationship edges failed", exc_info=True)

    data = {
        "traits": portrait.get("traits", []),
        "topics": portrait.get("topics", []),
        "stat_lines": rs.lines,
        "relationships": edges_data,
    }
    if prof is None:
        prof = AiProfile(user_id=user_id)
        session.add(prof)
    prof.summary = portrait.get("summary") or prof.summary
    prof.speech_style = portrait.get("speech") or prof.speech_style
    prof.data = data
    prof.stats = rs.stats
    prof.messages_seen = msg_count
    prof.refreshed_at = now_utc()
    await session.flush()
    return prof


async def sweep_active(
    session: AsyncSession,
    *,
    since_minutes: int = 20,
    max_rebuilds: int = 25,
) -> int:
    """Пересобирает профили игроков, писавших за последние ``since_minutes``.

    Это «реалтайм» без задержки для ответов: фоновый свип держит досье свежим.
    Дебаунс внутри ``refresh_profile`` не даёт пересобирать одно и то же зря.

    ``max_rebuilds`` ограничивает число РЕАЛЬНЫХ пересборок за один цикл (каждая
    — это LLM-вызов): на холодном старте активных может быть много, и без капа
    один свип сделал бы сотни последовательных запросов к модели. Стейлые
    профили (давно/никогда не обновлялись) идут первыми. Коммитим по ходу, чтобы
    не держать одну длинную транзакцию и не копить блокировки.

    Возвращает число реально перестроенных профилей.
    """
    since = now_utc() - timedelta(minutes=since_minutes)
    # Стейлость первой: сначала те, у кого профиля нет (NULL), затем самые
    # старые refreshed_at — так кап тратится на самых «протухших».
    ids = (
        await session.execute(
            select(AiMessage.user_id)
            .outerjoin(AiProfile, AiProfile.user_id == AiMessage.user_id)
            .where(AiMessage.role == "chat")
            .where(AiMessage.user_id.is_not(None))
            .where(AiMessage.created_at >= since)
            .group_by(AiMessage.user_id, AiProfile.refreshed_at)
            .order_by(AiProfile.refreshed_at.asc().nulls_first())
        )
    ).scalars().all()
    rebuilt = 0
    for uid in ids:
        if rebuilt >= max_rebuilds:
            break
        try:
            prof = await session.get(AiProfile, uid)
            before = prof.refreshed_at if prof is not None else None
            prof = await refresh_profile(session, uid)
            if prof is not None and prof.refreshed_at != before:
                rebuilt += 1
                # Фиксируем по одному: освобождаем ресурсы и не копим длинную
                # транзакцию на весь (потенциально долгий) цикл свипа.
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.debug("sweep refresh failed for %s", uid, exc_info=True)
    return rebuilt


def setup_profile_sweep(scheduler, sessionmaker, *, minutes: int = 4) -> None:
    """Регистрирует фоновый свип профилей активных игроков."""

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                n = await sweep_active(session)
                await session.commit()
                if n:
                    logger.info("drun profiles: rebuilt %d", n)
        except Exception:  # noqa: BLE001
            logger.warning("drun profile sweep failed", exc_info=True)

    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="drun_profile_sweep",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
