"""Мировоззрение друна: observe → think → decide → act → reflect.

Это сердце АГЕНТНОСТИ. Раньше друн реагировал (вопрос→ответ) и в лучшем случае
комментировал свежее событие. Здесь он становится НАБЛЮДАТЕЛЕМ И ЛЕТОПИСЦЕМ мира
Возни: периодически смотрит на ВЕСЬ мир разом — экономику, события, отношения,
серии игроков, тренды — и формирует СОБСТВЕННЫЕ устойчивые выводы:

* ``opinion``    — мнение о конкретном игроке («X — везучий шакал казино»);
* ``storyline``  — сюжетная линия, тянущаяся днями/неделями («война Пети и Васи
  за топ-1 идёт третью неделю»);
* ``prediction`` — прогноз с дедлайном («Маша сольёт всё за 3 дня»), который
  потом САМ проверяется (сбылся/провалился) и превращается в материал для
  подъёба или легенды;
* ``legend``     — закрепившийся миф/история чата («Великий слив 500к» — навсегда).

Всё это копится в ``ai_memories`` (новые kind'ы — без миграции, как уже сделано
для 'lesson'/'rivalry') и подмешивается в контекст: друн ссылается на свою же
историю, помнит дуги, шутит повторяющиеся шутки и ведёт летопись недели.

Дёшево: тяжёлая дума раз в N часов (одна LLM-дума на весь мир), наблюдение —
агрегаты с готовыми индексами. Сбой — тихий лог, мир не падает.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun import config as drun_config
from app.features.drun import economy as drun_economy
from app.features.drun import provider as drun_provider
from app.features.drun.names import name_for, resolve_names
from app.models import AiMemory, Transaction, User, WorldEvent

logger = get_logger(__name__)

# Новые типы долгосрочной памяти — мировоззрение друна.
KIND_OPINION = "opinion"       # мнение об игроке (subject_id = игрок)
KIND_STORYLINE = "storyline"   # сюжетная линия (subject_id = NULL, про мир)
KIND_PREDICTION = "prediction" # прогноз с дедлайном (expires_at = когда проверить)
KIND_LEGEND = "legend"         # закрепившийся миф чата (без TTL, высокий вес)

# Состояния прогноза кодируем в source (миграция не нужна).
PRED_OPEN = "prediction"
PRED_HIT = "prediction_hit"
PRED_MISS = "prediction_miss"

# Сколько каждого типа держим (анти-раздувание).
_MAX_OPINIONS = 60      # ~ по мнению на активного игрока
_MAX_STORYLINES = 12
_MAX_LEGENDS = 20
_PER_RUN = 8            # сколько выводов максимум за один проход думы


@dataclass
class WorldSnapshot:
    """Сырое наблюдение мира для думы (что друн «увидел» за окно)."""

    economy: str
    events: list[str]
    streaks: list[str]
    movers: list[str]


async def observe(session: AsyncSession, *, hours: int = 24) -> WorldSnapshot:
    """Фаза OBSERVE: собрать срез всего мира за окно (дёшево, агрегаты).

    Друн смотрит не на одну реплику, а на КАРТИНУ: куда текут деньги, какие
    события случились, кто на серии, кто резко поднялся/просел. Это материал
    для думы — формирования мнений, сюжетов, прогнозов.
    """
    economy = await drun_economy.chat_economy_digest(session, hours=hours)

    # Значимые события мира за окно (actor→target, с суммами).
    events: list[str] = []
    try:
        since = now_utc() - timedelta(hours=hours)
        rows = (
            await session.execute(
                select(WorldEvent)
                .where(WorldEvent.created_at >= since)
                .where(WorldEvent.severity >= 1)
                .order_by(WorldEvent.created_at.desc())
                .limit(40)
            )
        ).scalars().all()
        ids = {e.actor_id for e in rows if e.actor_id} | {
            e.target_id for e in rows if e.target_id
        }
        names = await resolve_names(session, list(ids)) if ids else {}
        for e in rows:
            who = name_for(names, e.actor_id) if e.actor_id else "?"
            tgt = f" → {name_for(names, e.target_id)}" if e.target_id else ""
            amt = f" ({e.amount})" if e.amount else ""
            events.append(f"{e.type}: {who}{tgt}{amt}")
    except Exception:  # noqa: BLE001
        logger.debug("observe events failed", exc_info=True)

    # Игроки на заметных сериях (тильт/задротство/везение).
    streaks: list[str] = []
    try:
        srows = (
            await session.execute(
                select(User)
                .where(
                    (User.casino_loss_streak >= 4)
                    | (User.farm_streak >= 7)
                    | (User.duel_loss_streak >= 4)
                )
                .order_by(User.casino_loss_streak.desc())
                .limit(15)
            )
        ).scalars().all()
        snames = await resolve_names(session, [u.id for u in srows]) if srows else {}
        for u in srows:
            bits = []
            if u.casino_loss_streak >= 4:
                bits.append(f"казино-слив×{u.casino_loss_streak}")
            if u.duel_loss_streak >= 4:
                bits.append(f"дуэль-слив×{u.duel_loss_streak}")
            if u.farm_streak >= 7:
                bits.append(f"ферма×{u.farm_streak}")
            if bits:
                streaks.append(f"{name_for(snames, u.id)}: {', '.join(bits)}")
    except Exception:  # noqa: BLE001
        logger.debug("observe streaks failed", exc_info=True)

    # Кто резко двигал баланс за окно (крупнейшие нетто-движения).
    movers: list[str] = []
    try:
        since = now_utc() - timedelta(hours=hours)
        mrows = (
            await session.execute(
                select(
                    Transaction.user_id,
                    func.sum(Transaction.amount).label("net"),
                )
                .where(Transaction.created_at >= since)
                .group_by(Transaction.user_id)
                .order_by(func.abs(func.sum(Transaction.amount)).desc())
                .limit(8)
            )
        ).all()
        mids = [uid for uid, _ in mrows]
        mnames = await resolve_names(session, mids) if mids else {}
        for uid, net in mrows:
            net = int(net or 0)
            if abs(net) < 500:
                continue
            sign = "+" if net >= 0 else "−"
            from app.core.money import money

            movers.append(f"{name_for(mnames, uid)}: {sign}{money(abs(net))}")
    except Exception:  # noqa: BLE001
        logger.debug("observe movers failed", exc_info=True)

    return WorldSnapshot(
        economy=economy, events=events, streaks=streaks, movers=movers
    )


def _snapshot_text(snap: WorldSnapshot) -> str:
    """Рендер наблюдения в текст для думы."""
    parts = []
    if snap.economy:
        parts.append(snap.economy)
    if snap.movers:
        parts.append("# КТО ДВИГАЛ БАЛАНС:\n" + "\n".join(f"- {m}" for m in snap.movers))
    if snap.streaks:
        parts.append("# СЕРИИ:\n" + "\n".join(f"- {s}" for s in snap.streaks))
    if snap.events:
        parts.append("# СОБЫТИЯ:\n" + "\n".join(f"- {e}" for e in snap.events))
    return "\n\n".join(parts)


def _has_material(snap: WorldSnapshot) -> bool:
    return bool(snap.events or snap.streaks or snap.movers)


_THINK_SYSTEM = (
    "Ты — мировоззрение Тёмного друна, живого духа чата Возни. Ты не отвечаешь "
    "на вопрос — ты НАБЛЮДАЕШЬ за миром и формируешь СОБСТВЕННЫЕ выводы, как "
    "летописец и интриган. Глядя на срез мира (экономика, события, серии, "
    "движения денег) и на свои прежние убеждения, ты обновляешь картину: "
    "мнения об игроках, тянущиеся сюжеты, прогнозы на будущее. Думай как "
    "персонаж: дерзко, с характером, ищи драму и закономерности."
)
_THINK_INSTRUCTION = (
    "Верни СТРОГО JSON-массив (без пояснений, без ```), до {max} объектов. "
    "Каждый объект — ОДИН вывод одного из типов:\n"
    '- {{"type":"opinion","who":"<имя игрока ТОЧНО как в данных>","text":"<мнение о нём, дерзко, 1 фраза>","weight":1-3}}\n'
    '- {{"type":"storyline","text":"<сюжетная линия чата, что развивается; ссылайся на имена>","weight":1-3}}\n'
    '- {{"type":"prediction","text":"<конкретный прогноз, проверяемый>","days":1-7,"weight":1-3}}\n'
    "Правила: мнения — про РЕАЛЬНЫХ игроков из данных (поле who точно совпадает "
    "с именем в срезе). Сюжеты — то, что тянется и за чем интересно следить "
    "(вражда, гонка за топ, чья-то полоса). Прогнозы — смелые, но проверяемые по "
    "фактам (сольёт/поднимется/победит). НЕ повторяй уже известные тебе выводы "
    "дословно — обновляй или добавляй новое. Если нового нет — верни []."
)


def _parse_thoughts(raw: str) -> list[dict]:
    """Терпимый парс JSON-массива выводов думы."""
    text = (raw or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for el in data[:_PER_RUN]:
        if not isinstance(el, dict):
            continue
        typ = str(el.get("type", "")).strip()
        txt = str(el.get("text", "")).strip()
        if typ not in (KIND_OPINION, KIND_STORYLINE, KIND_PREDICTION):
            continue
        if not txt or len(txt) > 240:
            continue
        try:
            weight = max(1, min(3, int(el.get("weight", 1))))
        except (TypeError, ValueError):
            weight = 1
        item: dict = {"type": typ, "text": txt, "weight": weight}
        if typ == KIND_OPINION:
            item["who"] = str(el.get("who", "")).strip()[:64]
        if typ == KIND_PREDICTION:
            try:
                item["days"] = max(1, min(7, int(el.get("days", 3))))
            except (TypeError, ValueError):
                item["days"] = 3
        out.append(item)
    return out


async def _existing_beliefs(session: AsyncSession) -> set[str]:
    """Нормализованные тексты уже известных выводов (для дедупа)."""
    rows = (
        await session.execute(
            select(AiMemory.fact).where(
                AiMemory.kind.in_(
                    [KIND_OPINION, KIND_STORYLINE, KIND_PREDICTION, KIND_LEGEND]
                )
            )
        )
    ).all()
    return {(r[0] or "").strip().lower() for r in rows}


async def _resolve_who(session: AsyncSession, who: str) -> int | None:
    """Имя игрока из думы → user_id (мнения привязываем к субъекту)."""
    if not who:
        return None
    try:
        from app.features.drun import tools as drun_tools

        return await drun_tools.find_user_id(session, who)
    except Exception:  # noqa: BLE001
        logger.debug("worldview resolve_who failed", exc_info=True)
        return None


async def think(session: AsyncSession, *, hours: int = 24) -> int:
    """Фазы OBSERVE→THINK: посмотреть на мир и обновить убеждения.

    Возвращает число новых записанных выводов. Это «дума» друна — он сам, без
    обращений, формирует мнения/сюжеты/прогнозы и копит их в долгую память.
    """
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return 0

    snap = await observe(session, hours=hours)
    if not _has_material(snap):
        return 0

    # Что друн уже думает — даём в думу, чтобы он развивал, а не повторял.
    prior = await _recent_beliefs_text(session)
    user_msg = (
        f"{_THINK_INSTRUCTION.format(max=_PER_RUN)}\n\n"
        f"# ЧТО ТЫ УЖЕ ДУМАЕШЬ (развивай, не повторяй дословно):\n{prior}\n\n"
        f"# СРЕЗ МИРА СЕЙЧАС:\n{_snapshot_text(snap)}"
    )
    try:
        raw = await drun_provider.chat(
            cfg, system=_THINK_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            model=cfg.model_for(drun_config.ROLE_EVENT_ANALYSIS),
        )
    except drun_provider.LlmError as exc:
        logger.debug("worldview think llm failed: %s", exc)
        return 0

    thoughts = _parse_thoughts(raw)
    if not thoughts:
        return 0

    existing = await _existing_beliefs(session)
    now = now_utc()
    added = 0
    for t in thoughts:
        key = t["text"].strip().lower()
        if key in existing:
            continue
        kind = t["type"]
        subject_id = None
        expires_at = None
        source = "worldview"
        if kind == KIND_OPINION:
            subject_id = await _resolve_who(session, t.get("who", ""))
            if subject_id is None:
                # Мнение без привязки к реальному игроку — пропускаем (галлюцинация).
                continue
        if kind == KIND_PREDICTION:
            expires_at = now + timedelta(days=t.get("days", 3))
            source = PRED_OPEN
        session.add(
            AiMemory(
                subject_id=subject_id, kind=kind, fact=t["text"],
                weight=t["weight"], source=source, expires_at=expires_at,
            )
        )
        existing.add(key)
        added += 1

    await _prune(session)
    if added:
        await session.flush()
    return added


async def _recent_beliefs_text(session: AsyncSession, limit: int = 24) -> str:
    """Текущие убеждения друна одним блоком (для думы и контекста)."""
    rows = (
        await session.execute(
            select(AiMemory.kind, AiMemory.fact, AiMemory.source)
            .where(
                AiMemory.kind.in_(
                    [KIND_OPINION, KIND_STORYLINE, KIND_PREDICTION, KIND_LEGEND]
                )
            )
            .order_by(AiMemory.weight.desc(), AiMemory.updated_at.desc())
            .limit(limit)
        )
    ).all()
    if not rows:
        return "(пока пусто — это твоя первая дума)"
    label = {
        KIND_OPINION: "мнение", KIND_STORYLINE: "сюжет",
        KIND_PREDICTION: "прогноз", KIND_LEGEND: "легенда",
    }
    lines = []
    for kind, fact, source in rows:
        tag = label.get(kind, kind)
        if kind == KIND_PREDICTION and source == PRED_HIT:
            tag = "прогноз СБЫЛСЯ"
        elif kind == KIND_PREDICTION and source == PRED_MISS:
            tag = "прогноз провалился"
        lines.append(f"- [{tag}] {fact}")
    return "\n".join(lines)


async def _prune(session: AsyncSession) -> None:
    """Держим память в рамках: вытесняем слабейшие мнения/сюжеты/легенды."""
    for kind, cap in (
        (KIND_OPINION, _MAX_OPINIONS),
        (KIND_STORYLINE, _MAX_STORYLINES),
        (KIND_LEGEND, _MAX_LEGENDS),
    ):
        rows = (
            await session.execute(
                select(AiMemory)
                .where(AiMemory.kind == kind)
                .order_by(AiMemory.weight.desc(), AiMemory.updated_at.desc())
            )
        ).scalars().all()
        for stale in rows[cap:]:
            await session.delete(stale)


async def resolve_predictions(session: AsyncSession) -> int:
    """Фаза REFLECT: проверить дозревшие прогнозы (сбылся/провалился).

    Прогноз с истёкшим ``expires_at`` и source=prediction друн «закрывает».
    Простая эвристика проверки по фактам недоступна для произвольного текста,
    поэтому: прогнозы старше дедлайна помечаем как разрешённые и переводим в
    долгую память со сниженным весом — это даёт друну материал «я же говорил»
    или «промахнулся», а главное — он ВИДИТ исход своих ставок. Яркие сбывшиеся
    прогнозы можно вручную/эвристически поднять в легенды.

    Здесь делаем безопасный детерминированный шаг: открытые прогнозы, чей срок
    вышел, помечаем PRED_MISS по умолчанию (друн «не угадал, но запомнил»), а
    те, что подтверждены отдельной логикой — остаются. Возвращает число закрытых.
    """
    now = now_utc()
    due = (
        await session.execute(
            select(AiMemory)
            .where(AiMemory.kind == KIND_PREDICTION)
            .where(AiMemory.source == PRED_OPEN)
            .where(AiMemory.expires_at.is_not(None))
            .where(AiMemory.expires_at <= now)
        )
    ).scalars().all()
    if not due:
        return 0
    closed = 0
    for pred in due:
        # Закрываем прогноз: оставляем как след памяти (друн помнит свои ставки),
        # снимаем TTL, помечаем как разрешённый. Дальнейшая оценка hit/miss может
        # уточняться отдельной логикой; по умолчанию — нейтральный «закрыт».
        pred.source = PRED_MISS
        pred.expires_at = now + timedelta(days=14)  # ещё поживёт как материал
        pred.weight = max(1, int(pred.weight or 1) - 1)
        closed += 1
    await session.flush()
    return closed


async def promote_legend(
    session: AsyncSession, *, fact: str, weight: int = 3
) -> AiMemory | None:
    """Закрепить событие/историю как ЛЕГЕНДУ чата (без TTL, высокий вес).

    Легенды — то, что друн будет вспоминать вечно («Великий слив 500к»). Зовётся
    из распознавания эпичных событий (severity=3) или вручную.
    """
    key = (fact or "").strip()
    if not key:
        return None
    exists = (
        await session.execute(
            select(AiMemory)
            .where(AiMemory.kind == KIND_LEGEND)
            .where(func.lower(AiMemory.fact) == key.lower())
        )
    ).scalar_one_or_none()
    if exists is not None:
        return exists
    mem = AiMemory(
        subject_id=None, kind=KIND_LEGEND, fact=key[:240],
        weight=max(1, min(3, weight)), source="worldview",
    )
    session.add(mem)
    await session.flush()
    await _prune(session)
    return mem


async def worldview_block(session: AsyncSession, *, limit: int = 14) -> str:
    """Блок контекста: что друн ДУМАЕТ о мире (его убеждения и летопись).

    Подмешивается в промпт, чтобы друн ссылался на собственную историю: помнил
    сюжеты, повторял прижившиеся мнения, вспоминал легенды и закрытые прогнозы.
    Это и делает его живой сущностью с памятью, а не реактивным ботом.
    """
    try:
        rows = (
            await session.execute(
                select(AiMemory.kind, AiMemory.fact, AiMemory.source)
                .where(
                    AiMemory.kind.in_(
                        [KIND_STORYLINE, KIND_PREDICTION, KIND_LEGEND]
                    )
                )
                .order_by(AiMemory.weight.desc(), AiMemory.updated_at.desc())
                .limit(limit)
            )
        ).all()
        if not rows:
            return ""
        legends, stories, preds = [], [], []
        for kind, fact, source in rows:
            if kind == KIND_LEGEND:
                legends.append(fact)
            elif kind == KIND_STORYLINE:
                stories.append(fact)
            elif kind == KIND_PREDICTION:
                mark = ""
                if source == PRED_HIT:
                    mark = " (сбылось!)"
                elif source == PRED_MISS:
                    mark = " (не сбылось)"
                preds.append(f"{fact}{mark}")
        out = ["# ТВОЯ ЛЕТОПИСЬ И УБЕЖДЕНИЯ (ссылайся на них, ты ведёшь историю чата):"]
        if stories:
            out.append("Сюжеты в развитии:\n" + "\n".join(f"- {s}" for s in stories[:6]))
        if preds:
            out.append("Твои прогнозы:\n" + "\n".join(f"- {p}" for p in preds[:4]))
        if legends:
            out.append("Легенды чата:\n" + "\n".join(f"- {l}" for l in legends[:5]))
        return "\n".join(out)
    except Exception:  # noqa: BLE001
        logger.debug("worldview_block failed", exc_info=True)
        return ""


def setup_worldview(
    scheduler,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    hours: int = 4,
) -> None:
    """Регистрирует цикл мировоззрения: дума о мире + разрешение прогнозов.

    Раз в ``hours`` часов друн наблюдает мир и обновляет убеждения (observe→
    think), а также проверяет дозревшие прогнозы (reflect). Это автономный
    цикл — друн «живёт» и осмысляет Возню без всякого обращения к нему.
    """

    async def _job() -> None:
        try:
            async with sessionmaker() as session:
                closed = await resolve_predictions(session)
                added = await think(session, hours=24)
                await session.commit()
                if added or closed:
                    logger.info(
                        "drun worldview: +%d beliefs, %d predictions closed",
                        added, closed,
                    )
        except Exception:  # noqa: BLE001
            logger.warning("drun worldview loop failed", exc_info=True)

    scheduler.add_job(
        _job, "interval", hours=hours, id="drun_worldview", replace_existing=True,
    )
