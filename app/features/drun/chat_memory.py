"""LLM-дистилляция «живой» памяти из чата: о чём говорят, кто есть кто.

Дешёвая событийная дистилляция (``distill.py``) умеет только статистику дуэлей/
казино/браков. Этого мало — друн не помнит ТЕМЫ разговоров, характеры и
отношения между людьми, поэтому ощущается как бот.

Здесь раз в N минут берём свежую болтовню чата и просим модель вытащить
несколько устойчивых фактов вида «X постоянно ноет про подкрутку», «Y и Z
кореша», «W фанатеет по кейсам». Факты кладём в ``ai_memories`` (source='chat')
с TTL, чтобы старое выветривалось. Сбой — тихий лог, мир не падает.
"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun import config as drun_config
from app.features.drun import memory as drun_memory
from app.features.drun import provider as drun_provider
from app.models import AiMemory, AiProfile

logger = get_logger(__name__)

# Сколько свежих реплик чата отдаём модели на анализ.
_CHAT_WINDOW = 60
# Сколько фактов максимум просим вернуть за проход.
_MAX_FACTS = 8
# TTL «живых» фактов: разговорное выветривается за неделю, если не подтвердится.
_FACT_TTL_DAYS = 7

_SYSTEM = (
    "Ты — аналитик чата. По логу болтовни выдели УСТОЙЧИВЫЕ наблюдения про "
    "людей: о чём человек постоянно говорит, его характер/манера, отношения и "
    "союзы/конфликты между людьми, привычки в игре, а также КЛИЧКИ которые "
    "люди дают друг другу, общие ШУТКИ и МЕМЫ чата. Игнорируй разовый шум, "
    "команды и мусор. Только то, что реально повторяется или ярко характеризует."
)
_INSTRUCTION = (
    "Верни СТРОГО JSON-массив (без пояснений) до {max} объектов вида "
    '{{"name":"ник","category":"тип","fact":"короткий факт на русском",'
    '"weight":1-3,"alias_of":"ник кого этим прозвищем зовут (только для '
    'category=nickname, иначе пусто)","alias":"само прозвище/кличка (только '
    'для category=nickname)"}}. '
    "category — одно из: trait (черта характера), topic (постоянная тема), "
    "nickname (кличка/прозвище кого-то), joke (повторяющаяся шутка), meme "
    "(локальный мем чата), relationship (связь/конфликт/союз), habit (игровая "
    "привычка). "
    "Для nickname ОБЯЗАТЕЛЬНО заполни alias_of (кого так зовут, его обычный "
    "ник из лога) и alias (как именно зовут). Пример: если Vasya777 в чате "
    "зовут «Артёмом» — name='Vasya777', alias_of='Vasya777', alias='Артём'. "
    "weight: 1 — мелочь, 2 — заметная черта, 3 — яркая определяющая черта. "
    "Если ничего стоящего нет — верни []. Факт — это про человека/чат, не про "
    "конкретное сообщение. Пиши живым языком, как заметка для себя."
)

# Разрешённые категории (фолбэк → 'chat'). Хранятся в AiMemory.kind с префиксом.
_CATEGORIES = frozenset({
    "trait", "topic", "nickname", "joke", "meme", "relationship", "habit",
})


async def distill_chat(session: AsyncSession) -> int:
    """Один проход LLM-дистилляции памяти из чата. Возвращает число фактов."""
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return 0

    msgs = await drun_memory.recent_chat(session, channel="chat", limit=_CHAT_WINDOW)
    if len(msgs) < 8:  # мало данных — не дёргаем модель зря
        return 0

    lines = []
    # Имя может быть неуникальным (несколько игроков с одинаковым ником в окне).
    # Копим МНОЖЕСТВО id на имя, чтобы при коллизии не приписать факт не тому.
    name_to_ids: dict[str, set[int]] = {}
    for m in msgs:
        nm = (m.meta or {}).get("name") or f"id{m.user_id}"
        lines.append(f"{nm}: {m.content}")
        if m.user_id:
            name_to_ids.setdefault(nm.lower(), set()).add(m.user_id)

    log = "\n".join(lines)
    user_msg = (
        f"{_INSTRUCTION.format(max=_MAX_FACTS)}\n\n# ЛОГ ЧАТА\n{log}"
    )
    try:
        raw = await drun_provider.chat(
            cfg, system=_SYSTEM, messages=[{"role": "user", "content": user_msg}],
            model=cfg.model_for(drun_config.ROLE_MEMORY_EXTRACT),
        )
    except drun_provider.LlmError as exc:
        logger.debug("chat distill llm failed: %s", exc)
        return 0

    facts = _parse_facts(raw)
    if not facts:
        return 0

    existing = await _existing_chat_facts(session)
    expires = now_utc() + timedelta(days=_FACT_TTL_DAYS)
    added = 0
    reinforced = 0
    for item in facts:
        fact = item["fact"]
        ids = name_to_ids.get(item["name"].lower(), set())
        # Однозначное имя → привязываем к игроку; коллизия или неизвестное имя →
        # храним как факт про мир/чат (subject_id=None), но НЕ приписываем
        # конкретному человеку, чтобы не путать досье.
        subject_id = next(iter(ids)) if len(ids) == 1 else None
        cat = item.get("category", "")
        kind = f"chat:{cat}" if cat in _CATEGORIES else "chat"
        if (subject_id, fact) in existing:
            # ПОВТОРНОЕ упоминание — не шум, а ПОДТВЕРЖДЕНИЕ. Раньше дубликат
            # просто отбрасывался, и из-за TTL=7д прижившиеся клички/шутки/мемы
            # выветривались, даже если чат повторял их каждую неделю. Теперь
            # повтор УКРЕПЛЯЕТ память: бустим вес (до 3) и продлеваем жизнь —
            # так running joke живёт, пока он живой в чате, и умирает, когда чат
            # про него забыл. Это и есть community lore с естественным отбором.
            if await _reinforce_fact(session, subject_id, fact, expires):
                reinforced += 1
            continue
        existing.add((subject_id, fact))
        # Типизируем память (#3): kind = "chat:<category>" — так ники/шутки/мемы
        # отличимы и от обычных черт, и друг от друга.
        session.add(
            AiMemory(
                subject_id=subject_id,
                kind=kind,
                fact=fact,
                weight=item["weight"],
                source="chat",
                expires_at=expires,
            )
        )
        added += 1

    # Прозвища (#alias): привязываем выученные клички к ИГРОКУ, чтобы потом
    # резолвить обращения вроде «забань артёма». Копим в профиле, не дублируя.
    try:
        await _persist_aliases(session, facts, name_to_ids)
    except Exception:  # noqa: BLE001
        logger.debug("persist aliases failed", exc_info=True)

    if added or reinforced:
        await session.flush()
    return added


async def _reinforce_fact(
    session: AsyncSession,
    subject_id: int | None,
    fact: str,
    expires,
) -> bool:
    """Укрепляет уже известный факт: +1 к весу (до 3) и продление TTL.

    Возвращает True, если запись найдена и обновлена. Так повторяющиеся клички/
    шутки/мемы накапливают вес (становятся «ярче» в контексте) и не протухают,
    пока чат их повторяет. Сбой — молча False (укрепление не критично).
    """
    try:
        if subject_id is None:
            q = select(AiMemory).where(
                AiMemory.fact == fact, AiMemory.subject_id.is_(None)
            ).limit(1)
        else:
            q = select(AiMemory).where(
                AiMemory.fact == fact, AiMemory.subject_id == subject_id
            ).limit(1)
        mem = (await session.execute(q)).scalar_one_or_none()
        if mem is None:
            return False
        mem.weight = min(3, int(mem.weight or 1) + 1)
        # Продлеваем жизнь только TTL-фактам (легенды/мнения без срока не трогаем).
        if mem.expires_at is not None:
            mem.expires_at = expires
        return True
    except Exception:  # noqa: BLE001
        logger.debug("reinforce_fact failed", exc_info=True)
        return False


async def _persist_aliases(
    session: AsyncSession,
    facts: list[dict],
    name_to_ids: dict[str, set[int]],
) -> None:
    """Сохраняет выученные прозвища в профили соответствующих игроков.

    Для каждого факта-клички берём ``alias_of`` (кого так зовут) → находим его
    user_id по логу окна → дописываем алиас в ``AiProfile.data["aliases"]`` с
    накоплением веса. Привязываем только при ОДНОЗНАЧНОМ совпадении ника, чтобы
    не приклеить кличку не тому при коллизии имён.
    """
    from app.features.drun import aliases as drun_aliases

    # alias_of (ник) → набор предложенных кличек.
    target_to_aliases: dict[str, list[str]] = {}
    for item in facts:
        if item.get("category") != "nickname":
            continue
        alias = item.get("alias") or ""
        target = (item.get("alias_of") or item.get("name") or "").strip()
        if alias and target:
            target_to_aliases.setdefault(target.lower(), []).append(alias)

    for target_name, new_aliases in target_to_aliases.items():
        ids = name_to_ids.get(target_name, set())
        if len(ids) != 1:
            continue  # неоднозначно или неизвестно — не рискуем
        uid = next(iter(ids))
        prof = await session.get(AiProfile, uid)
        if prof is None:
            continue
        data = dict(prof.data or {})
        data["aliases"] = drun_aliases.add_aliases(
            data.get("aliases"), new_aliases
        )
        prof.data = data


def _parse_facts(raw: str) -> list[dict]:
    """Парсит JSON-массив фактов из ответа модели, терпимо к мусору."""
    text = (raw or "").strip()
    # выдёргиваем массив, даже если модель обернула его текстом/```
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    out: list[dict] = []
    if not isinstance(data, list):
        return []
    for el in data[:_MAX_FACTS]:
        if not isinstance(el, dict):
            continue
        name = str(el.get("name", "")).strip()
        fact = str(el.get("fact", "")).strip()
        if not name or not fact or len(fact) > 200:
            continue
        try:
            weight = int(el.get("weight", 1))
        except (TypeError, ValueError):
            weight = 1
        weight = max(1, min(3, weight))
        category = str(el.get("category", "")).strip().lower()
        out.append({
            "name": name, "fact": fact, "weight": weight, "category": category,
            "alias_of": str(el.get("alias_of", "")).strip(),
            "alias": str(el.get("alias", "")).strip(),
        })
    return out


async def _existing_chat_facts(session: AsyncSession) -> set[tuple[int | None, str]]:
    """Уже сохранённые НЕ протухшие (subject_id, fact) — грубая дедупликация.

    Ограничиваем выборку живыми записями (``expires_at`` пуст или в будущем),
    чтобы дедуп-сет не рос вместе с накопленной протухшей памятью.
    """
    now = now_utc()
    rows = (
        await session.execute(
            select(AiMemory.subject_id, AiMemory.fact).where(
                or_(AiMemory.expires_at.is_(None), AiMemory.expires_at > now)
            )
        )
    ).all()
    return {(r[0], r[1]) for r in rows}


async def purge_expired(session: AsyncSession) -> int:
    """Физически удаляет протухшие факты (``expires_at`` в прошлом).

    Без этого таблица ``ai_memories`` только растёт: read-фильтр прячет
    протухшее, но не освобождает место. Возвращает число удалённых строк.
    """
    now = now_utc()
    result = await session.execute(
        delete(AiMemory).where(
            AiMemory.expires_at.is_not(None), AiMemory.expires_at <= now
        )
    )
    return int(result.rowcount or 0)


# --- Извлечение СОЦИАЛЬНЫХ ЭПИЗОДОВ (LEAP-5) ---------------------------------

_EPISODE_WINDOW = 80
_MAX_EPISODES = 5

_EPISODE_SYSTEM = (
    "Ты — наблюдатель социальной динамики чата. По логу болтовни найди ПАМЯТНЫЕ "
    "социальные МОМЕНТЫ между людьми — не черты и не темы, а конкретные "
    "ПОСТУПКИ: кто кого предал, кинул с обещанием, заступился, унизил, бросил "
    "вызов, помирился, проявил щедрость/лидерство, ныл. Игнорируй обычную "
    "болтовню, шутки и фон — только то, что реально характеризует ОТНОШЕНИЯ и "
    "стоит запомнить надолго."
)
_EPISODE_INSTRUCTION = (
    "Верни СТРОГО JSON-массив (без пояснений) до {max} объектов вида "
    '{{"name":"ник того, КТО совершил поступок (точно из лога)",'
    '"type":"тип эпизода","gist":"суть в одну фразу на русском, КОНКРЕТНО, '
    'чтобы можно было припомнить дословно","significance":1-3}}. '
    "type — одно из: betrayal (предал/кинул), broken_promise (обещал и слил), "
    "kept_promise (сдержал слово), promise (дал обещание), support (поддержал), "
    "defense (заступился за кого-то), generosity (расщедрился, подарил), "
    "leadership (повёл за собой, организовал), humiliation (публично унизил "
    "кого-то / был унижен — пиши про того, ВОКРУГ кого момент), challenge "
    "(бросил вызов), rivalry_escalation (вражда обострилась), reconciliation "
    "(помирились), whining (ноет/жалуется по кругу). "
    "significance: 1 — мелкий момент, 2 — заметный, 3 — яркий, запоминающийся "
    "надолго. Бери ТОЛЬКО реальные моменты из лога, не выдумывай. Если памятных "
    "поступков нет — верни []."
)


def _parse_episodes(raw: str) -> list[dict]:
    """Парсит JSON-массив эпизодов из ответа модели, терпимо к мусору."""
    from app.features.drun import episodes as drun_episodes

    text = (raw or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for el in data[:_MAX_EPISODES]:
        if not isinstance(el, dict):
            continue
        name = str(el.get("name", "")).strip()
        etype = str(el.get("type", "")).strip().lower()
        gist = str(el.get("gist", "")).strip()
        if not name or not gist or len(gist) > 200:
            continue
        if drun_episodes.episode_type(etype) is None:
            continue
        try:
            sig = int(el.get("significance", 1))
        except (TypeError, ValueError):
            sig = 1
        out.append({
            "name": name, "type": etype, "gist": gist,
            "significance": max(1, min(3, sig)),
        })
    return out


async def distill_episodes(session: AsyncSession) -> int:
    """Один проход извлечения социальных эпизодов из чата. Возвращает число.

    Отдельный LLM-проход (а не довесок к фактам): эпизоды требуют другого
    фокуса — не «какой человек», а «что он СДЕЛАЛ». Привязываем к игроку только
    при ОДНОЗНАЧНОМ совпадении ника (иначе момент не на того — хуже, чем
    пропустить). Запись + прямой сдвиг мнения — через episodes.record_episode.
    """
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return 0

    from app.features.drun import episodes as drun_episodes

    msgs = await drun_memory.recent_chat(session, channel="chat", limit=_EPISODE_WINDOW)
    if len(msgs) < 10:
        return 0

    lines = []
    name_to_ids: dict[str, set[int]] = {}
    for m in msgs:
        nm = (m.meta or {}).get("name") or f"id{m.user_id}"
        lines.append(f"{nm}: {m.content}")
        if m.user_id:
            name_to_ids.setdefault(nm.lower(), set()).add(m.user_id)

    log = "\n".join(lines)
    user_msg = (
        f"{_EPISODE_INSTRUCTION.format(max=_MAX_EPISODES)}\n\n# ЛОГ ЧАТА\n{log}"
    )
    try:
        raw = await drun_provider.chat(
            cfg, system=_EPISODE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            model=cfg.model_for(drun_config.ROLE_MEMORY_EXTRACT),
        )
    except drun_provider.LlmError as exc:
        logger.debug("episode distill llm failed: %s", exc)
        return 0

    items = _parse_episodes(raw)
    if not items:
        return 0

    added = 0
    for it in items:
        ids = name_to_ids.get(it["name"].lower(), set())
        if len(ids) != 1:
            continue  # неоднозначно/неизвестно — момент не на того хуже пропуска
        uid = next(iter(ids))
        mem = await drun_episodes.record_episode(
            session,
            subject_id=uid,
            code=it["type"],
            gist=it["gist"],
            significance=it["significance"],
        )
        if mem is not None:
            added += 1
    return added


def setup_chat_distill(
    scheduler,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    minutes: int = 45,
) -> None:
    """Регистрирует периодическую LLM-дистилляцию памяти из чата."""

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                removed = await purge_expired(session)
                from app.features.drun import memory as drun_memory

                pruned = await drun_memory.prune_old_messages(session)
                n = await distill_chat(session)
                eps = await distill_episodes(session)
                await session.commit()
                if n or removed or pruned or eps:
                    logger.info(
                        "drun chat memory: +%d facts, +%d episodes, -%d expired, -%d old msgs",
                        n, eps, removed, pruned,
                    )
        except Exception:  # noqa: BLE001
            logger.warning("drun chat distill failed", exc_info=True)

    scheduler.add_job(
        _job,
        "interval",
        minutes=minutes,
        id="drun_chat_distill",
        replace_existing=True,
    )
