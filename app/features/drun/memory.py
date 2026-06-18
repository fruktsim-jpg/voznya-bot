"""Память друна: краткосрочная (история) и долгосрочная (факты).

* Краткосрочная — таблица ``ai_messages``: последние реплики диалога/постов в
  канале. Используется как контекст «о чём недавно говорили» и анти-повтор.
* Долгосрочная — таблица ``ai_memories``: устойчивые факты об игроках/мире
  («X — самый богатый», «Y слил 500к»). Подмешиваются в контекст по весу.

Память пишет только друн/бот. Никаких FK (соглашение проекта).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, literal, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun import embeddings as drun_embeddings
from app.models import AiMemory, AiMessage

logger = get_logger(__name__)

# Сколько символов реплики игрока храним (анти-раздувание контекста/токенов).
_CHAT_MAX_CHARS = 320
# Сколько дней держим краткосрочную историю (ai_messages). Все чтения этой
# таблицы — оконные (последние реплики/счётчики за минуты), поэтому старше
# нескольких дней данные не нужны, а таблица иначе растёт безгранично.
_MESSAGES_RETENTION_DAYS = 14

# --- Краткосрочная память (история) -----------------------------------------


async def capture_chat(
    session: AsyncSession,
    *,
    user_id: int,
    name: str,
    content: str,
    channel: str = "chat",
    media: str | None = None,
    reply_to_name: str | None = None,
    reply_to_bot: bool = False,
    reply_excerpt: str | None = None,
) -> AiMessage | None:
    """Сохраняет реплику живого игрока (role='chat') с ником в meta.

    Возвращает запись или ``None``, если сообщение пустое после обрезки. Имя
    кладём в ``meta.name`` — это снимок на момент сообщения (ник мог смениться).
    Commit — на вызывающем (middleware фиксирует сессию после хендлера).

    Друн — наблюдатель, а не Q&A-бот: он должен ВИДЕТЬ форму сообщения, а не
    только текст. Поэтому в ``meta`` кладём восприятие:
    * ``media`` — тип вложения (photo/sticker/voice/video/...), если это не
      просто текст; благодаря этому пустые по тексту медиа тоже фиксируются;
    * ``reply_to_name`` / ``reply_to_bot`` / ``reply_excerpt`` — структура
      ответа: кому отвечал автор (или это ответ самому друну) и на какой текст,
      чтобы друн понимал нить беседы, а не плоский список реплик.
    """
    from app.features.drun.actions import sanitize_user_text

    # Defense-in-depth: обезвреживаем econ-директивы в НЕДОВЕРЕННОМ вводе игрока
    # прямо на границе памяти (см. ниже). Чистим до любых проверок длины.
    text = sanitize_user_text((content or "").strip())
    # Медиа без подписи — это всё равно событие в чате: фиксируем плейсхолдером,
    # чтобы друн видел «кинул стикер/голосовуху», а не пустоту.
    if not text and not media:
        return None
    if len(text) > _CHAT_MAX_CHARS:
        text = text[: _CHAT_MAX_CHARS - 1].rstrip() + "…"
    meta: dict[str, Any] = {"name": name}
    if media:
        meta["media"] = media
    if reply_to_bot:
        meta["reply_to_bot"] = True
    if reply_to_name:
        # reply_to_name — ник чужого сообщения (недоверенный источник), reply_excerpt
        # — текст процитированной реплики. Оба рендерятся в _chat_block и уходят в
        # LLM, поэтому калечим econ-директивы так же, как основной content (иначе
        # дыра в defense-in-depth: [[econ:...]] могло бы доехать в контекст через
        # цитату/ник в обход санитайзинга).
        meta["reply_to"] = sanitize_user_text(reply_to_name)[:64]
    if reply_excerpt:
        meta["reply_excerpt"] = sanitize_user_text(reply_excerpt.strip())[:120]
    msg = AiMessage(
        role="chat",
        content=text or f"[{media}]",
        channel=channel,
        user_id=user_id,
        meta=meta,
    )
    session.add(msg)
    await session.flush()
    return msg


async def add_message(
    session: AsyncSession,
    *,
    role: str,
    content: str,
    channel: str = "chat",
    user_id: int | None = None,
    trigger_event_id: int | None = None,
    tokens: int | None = None,
    meta: dict[str, Any] | None = None,
) -> AiMessage:
    """Записывает реплику в историю. Commit — на вызывающем."""
    msg = AiMessage(
        role=role,
        content=content,
        channel=channel,
        user_id=user_id,
        trigger_event_id=trigger_event_id,
        tokens=tokens,
        meta=meta or {},
    )
    session.add(msg)
    await session.flush()
    return msg


async def prune_old_messages(
    session: AsyncSession,
    *,
    days: int = _MESSAGES_RETENTION_DAYS,
    batch_size: int = 5000,
    max_batches: int = 20,
) -> int:
    """Удаляет краткосрочную историю старше ``days`` дней. Commit — на вызывающем.

    ``ai_messages`` — самая высоконагруженная таблица (каждое сообщение чата), а
    все её чтения оконные (минуты/последние N строк). Без ретенции она растёт
    без предела и замедляет каждый оконный COUNT/scan.

    Удаляем ПАЧКАМИ (по ``batch_size`` строк, не более ``max_batches`` за вызов):
    на первом запуске таблица может быть огромной и без ретенции копилась долго —
    единый ``DELETE`` залочил бы таблицу надолго и раздул транзакцию/WAL. За один
    тик чистим ограниченный объём, остаток догоняется на следующих прогонах джобы.
    Возвращает число удалённых строк.
    """
    cutoff = now_utc() - timedelta(days=max(1, days))
    total = 0
    for _ in range(max(1, max_batches)):
        # Подзапрос с LIMIT: удаляем порцию по первичному ключу, чтобы не держать
        # длинный лок на весь матч предиката за один statement.
        ids_subq = (
            select(AiMessage.id)
            .where(AiMessage.created_at < cutoff)
            .limit(batch_size)
        )
        result = await session.execute(
            delete(AiMessage).where(AiMessage.id.in_(ids_subq))
        )
        deleted = int(result.rowcount or 0)
        total += deleted
        if deleted < batch_size:
            break
    return total


async def recent_messages(
    session: AsyncSession, *, channel: str = "chat", limit: int = 10
) -> list[AiMessage]:
    """Последние ходы РЕАЛЬНОГО диалога (человек ↔ друн) в хронологии.

    Только обмены, помеченные ``meta.kind == 'reply'`` — то есть когда игрок
    обратился и друн ответил. Автономные вкиды/реакции (монологи про мир) сюда
    НЕ попадают: иначе друн, видя свою же ленту рофлов, продолжает её и
    игнорирует короткий вопрос собеседника.

    ВАЖНО: в диалоге участвуют РАЗНЫЕ люди. У каждого user-хода в ``content``
    уже стоит префикс с именем автора, а у assistant-хода — ``meta.to_name``
    (кому друн отвечал). Вызывающий использует это, чтобы модель не путала, кто
    кому что писал.
    """
    rows = (
        await session.execute(
            select(AiMessage)
            .where(AiMessage.channel == channel)
            .where(AiMessage.role.in_(("user", "assistant")))
            .where(AiMessage.meta["kind"].astext == "reply")
            .order_by(AiMessage.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(reversed(rows))


async def recent_chat(
    session: AsyncSession, *, channel: str = "chat", limit: int = 14
) -> list[AiMessage]:
    """Последняя «болтовня» живых игроков (role='chat') в хронологии.

    Это сырые реплики чата, которые пишет middleware — отдельно от диалоговых
    user/assistant-ходов друна. Нужны, чтобы друн видел, о чём говорят люди.
    """
    rows = (
        await session.execute(
            select(AiMessage)
            .where(AiMessage.channel == channel)
            .where(AiMessage.role == "chat")
            .order_by(AiMessage.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(reversed(rows))


async def recent_self_posts(
    session: AsyncSession, *, channel: str = "chat", limit: int = 6
) -> list[str]:
    """Последние СОБСТВЕННЫЕ реплики друна (role='assistant') — для анти-повтора.

    Возвращает только тексты (новые первыми), чтобы подмешать в контекст
    «вот что ты уже говорил, не повторяй зачины/обороты/жертв».
    """
    rows = (
        await session.execute(
            select(AiMessage.content)
            .where(AiMessage.channel == channel)
            .where(AiMessage.role == "assistant")
            .order_by(AiMessage.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [r for r in rows if r]


async def recent_chat_count(
    session: AsyncSession, *, channel: str = "chat", seconds: int = 180
) -> int:
    """Сколько живых реплик игроков было за последние ``seconds`` секунд.

    Индикатор «движа» в чате: используется, чтобы случайные вкиды друна
    случались только когда есть о чём говорить, а не в мёртвой тишине.
    """
    since = now_utc() - timedelta(seconds=seconds)
    total = await session.scalar(
        select(func.count())
        .select_from(AiMessage)
        .where(AiMessage.channel == channel)
        .where(AiMessage.role == "chat")
        .where(AiMessage.created_at >= since)
    )
    return int(total or 0)


async def count_replies_today(
    session: AsyncSession, *, channel: str = "chat"
) -> int:
    """Сколько реплик друн (role='assistant') выдал за последние сутки.

    Используется как дневной кап (``posts_per_day_max``), чтобы друн не
    превратился в спамера и не сжёг токены.
    """
    since = now_utc() - timedelta(days=1)
    total = await session.scalar(
        select(func.count())
        .select_from(AiMessage)
        .where(AiMessage.channel == channel)
        .where(AiMessage.role == "assistant")
        .where(AiMessage.created_at >= since)
    )
    return int(total or 0)


async def pulse_stats(
    session: AsyncSession, *, channel: str = "chat", minutes: int = 15
) -> tuple[int, int]:
    """Пульс чата за окно: (всего реплик игроков, уникальных авторов).

    Нужно activity-governor'у, чтобы отличать «один человек долбит бота» от
    «много людей реально общаются». Один проход по окну, без подтягивания строк.
    """
    since = now_utc() - timedelta(minutes=max(1, minutes))
    base = (
        select(AiMessage.user_id)
        .where(AiMessage.channel == channel)
        .where(AiMessage.role == "chat")
        .where(AiMessage.created_at >= since)
    ).subquery()
    # Оба агрегата за один проход/round-trip по тому же окну.
    row = (
        await session.execute(
            select(func.count(), func.count(func.distinct(base.c.user_id)))
            .select_from(base)
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


async def bot_replies_in_window(
    session: AsyncSession, *, channel: str = "chat", minutes: int = 15
) -> int:
    """Сколько раз друн (role='assistant') отвечал за окно ``minutes`` минут.

    Единый источник семантики «реплика бота» (role='assistant') — чтобы
    governor не держал свою копию COUNT-запроса и не разошёлся при смене схемы.
    """
    since = now_utc() - timedelta(minutes=max(1, minutes))
    total = await session.scalar(
        select(func.count())
        .select_from(AiMessage)
        .where(AiMessage.channel == channel)
        .where(AiMessage.role == "assistant")
        .where(AiMessage.created_at >= since)
    )
    return int(total or 0)


# --- Долгосрочная память (факты) --------------------------------------------


async def remember(
    session: AsyncSession,
    *,
    fact: str,
    subject_id: int | None = None,
    kind: str = "fact",
    weight: int = 1,
    source: str | None = "auto",
    expires_at: datetime | None = None,
) -> AiMemory:
    """Сохраняет факт в долгосрочную память. Commit — на вызывающем."""
    mem = AiMemory(
        subject_id=subject_id,
        kind=kind,
        fact=fact,
        weight=weight,
        source=source,
        expires_at=expires_at,
    )
    session.add(mem)
    await session.flush()
    # Best-effort embed на лету: если провайдер настроен и быстро ответил —
    # факт сразу попадёт в векторный поиск. Если нет — backfill-джоб подберёт
    # через несколько минут. Любой сбой embed-а игнорируем; запись уже сделана.
    try:
        vec = await drun_embeddings.embed_text(session, fact)
        if vec is not None:
            await drun_embeddings.save_embedding(session, mem.id, vec)
    except Exception:  # noqa: BLE001
        logger.debug("inline embed failed for memory id=%s", mem.id, exc_info=True)
    return mem


# --- Умный отбор памяти (Phase 3): вес × свежесть × релевантность теме --------

# Длина «хвоста значимости»: за столько дней вклад свежести падает вдвое.
_RECENCY_HALFLIFE_DAYS = 21.0
# Сколько кандидатов тянем из БД перед скорингом (берём с запасом, чтобы было
# из чего выбирать; скоринг в Python дешевле второго прохода SQL).
_CANDIDATE_POOL = 60
# Стоп-слова: не считаем их за «тему» при пересечении с запросом.
_STOPWORDS = frozenset({
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а",
    "то", "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же",
    "вы", "за", "бы", "по", "ее", "мне", "было", "вот", "от", "меня",
    "это", "о", "из", "ему", "теперь", "был", "до", "вас", "там",
    "для", "мы", "тебя", "их", "чем", "была", "сам", "чтоб", "без", "ли",
    "если", "уже", "или", "ни", "быть", "себя", "под", "будет", "кто",
    "этот", "того", "потому", "этого", "какой", "ну", "ее", "при", "this",
})


def _tokenize(text: str) -> set[str]:
    """Грубая токенизация в множество значимых слов (lowercase, без стоп-слов)."""
    out: set[str] = set()
    cur: list[str] = []
    for ch in (text or "").lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                w = "".join(cur)
                cur = []
                if len(w) >= 3 and w not in _STOPWORDS:
                    out.add(w)
    if cur:
        w = "".join(cur)
        if len(w) >= 3 and w not in _STOPWORDS:
            out.add(w)
    return out


def _score_memory(
    mem: AiMemory, query_tokens: set[str], now: datetime
) -> float:
    """Скор памяти = вес + свежесть + тематическое пересечение с запросом.

    * вес (weight) — базовая значимость, как было раньше;
    * свежесть — экспоненциальный спад по возрасту (полураспад ~3 недели),
      чтобы старые факты не вытесняли актуальные навсегда;
    * тема — сколько значимых слов запроса встречается в тексте факта; это
      поднимает «по теме» воспоминания именно к текущей реплике собеседника.
    """
    score = float(mem.weight or 0)
    # Свежесть: 0..~3 за недавность. Возраст считаем в дробных днях в обеих
    # ветках (naive — оборонительная: created_at у нас tz-aware).
    created = mem.created_at
    if created is not None:
        if created.tzinfo is None:
            age_days = max(
                0.0, (now.replace(tzinfo=None) - created).total_seconds() / 86400.0
            )
        else:
            age_days = max(0.0, (now - created).total_seconds() / 86400.0)
        score += 3.0 * (0.5 ** (age_days / _RECENCY_HALFLIFE_DAYS))
    # Тема: каждое совпавшее слово запроса в факте — весомый буст.
    if query_tokens:
        fact_tokens = _tokenize(mem.fact)
        overlap = len(query_tokens & fact_tokens)
        if overlap:
            score += 2.5 * overlap
    return score


async def scored_memories(
    session: AsyncSession,
    *,
    subject_id: int | None = None,
    query: str | None = None,
    limit: int = 8,
) -> list[AiMemory]:
    """Отбирает факты с учётом веса, свежести и релевантности теме ``query``.

    Единая точка отбора долгосрочной памяти для контекста. Ранжирование делается
    в Postgres гибридом ``ts_rank`` (BM25 по русской морфологии) + ``similarity``
    (pg_trgm, устойчив к опечаткам и коротким запросам) + вес + экспоненциальная
    свежесть. SQL-ранкинг сильнее старого Python-overlap'а и тянет ровно ``limit``
    строк вместо большого пула.

    При пустом ``query`` ранжирует по весу × свежесть (тематика отключается).
    Отсекает протухшие (``expires_at`` в прошлом).

    Безопасный fallback: если миграция ``0049`` ещё не накатана (нет колонки
    ``fact_tsv``) или Postgres не знает русский конфиг — переключаемся на
    старый Python-скоринг через пул кандидатов. Это исключает падение друна на
    старых окружениях.
    """
    now = now_utc()
    not_expired = or_(AiMemory.expires_at.is_(None), AiMemory.expires_at > now)
    if subject_id is not None:
        scope = or_(AiMemory.subject_id.is_(None), AiMemory.subject_id == subject_id)
    else:
        scope = AiMemory.subject_id.is_(None)

    q = (query or "").strip()
    try:
        return await _scored_memories_sql(
            session, scope=scope, not_expired=not_expired, query=q, limit=limit,
        )
    except DBAPIError as exc:
        # Скорее всего нет колонки fact_tsv (0049 не накачена) или нет
        # русского конфига. Логируем один раз на отладке и идём по старому пути.
        logger.debug("scored_memories SQL ranker unavailable, fallback: %s", exc)
        return await _scored_memories_python(
            session, scope=scope, not_expired=not_expired, query=q, limit=limit, now=now,
        )


async def _scored_memories_sql(
    session: AsyncSession,
    *,
    scope,
    not_expired,
    query: str,
    limit: int,
) -> list[AiMemory]:
    """Гибридный SQL-ранкинг: FTS(ts_rank) + trigram(similarity) + вес + свежесть.

    Score (взвешен, чтобы ни один компонент не доминировал):
    * ``weight`` × 1.0           — авторский вес факта (1..3)
    * recency_decay × 3.0        — 0.5^(дней/21), как и было
    * ts_rank × 6.0              — FTS-релевантность к запросу (русская морфология)
    * similarity × 3.0           — trigram (опечатки, короткие слова)

    Trigram-блок включается ТОЛЬКО если индекс присутствует (определяем по
    наличию расширения pg_trgm); иначе остаётся чистый FTS.
    """
    # Свежесть: 0.5 ^ (age_days / 21).  Считаем в SQL через extract(epoch from age).
    age_days = (
        func.greatest(
            literal(0.0),
            func.extract("epoch", func.now() - AiMemory.created_at) / 86400.0,
        )
    )
    recency = func.power(literal(0.5), age_days / literal(21.0))

    # Базовый score без тематики.
    base_score = AiMemory.weight + literal(3.0) * recency

    if not query:
        stmt = (
            select(AiMemory)
            .where(and_(not_expired, scope))
            .order_by(base_score.desc(), AiMemory.created_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    # FTS: tsquery через plainto_tsquery (терпим к произвольному вводу).
    # Конфиг 'russian' для запроса — миграция создаёт fact_tsv по тому же конфигу.
    # Если в окружении нет 'russian', DBAPIError всплывёт и сработает fallback.
    tsq = func.plainto_tsquery(literal("russian"), literal(query))
    ts_rank = func.ts_rank(text("fact_tsv"), tsq)

    # similarity() требует pg_trgm. Проверяем наличие расширения один раз.
    has_trgm = bool(
        await session.scalar(
            select(literal(1)).where(
                text("EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm')")
            )
        )
    )

    # Vector-блок (LEAP-1): семантическое сходство embedding запроса с
    # embedding факта. Это ловит синонимы/перифразы/морфологию там, где
    # BM25 (точные токены) и trigram (символьное сходство) пасуют:
    # «слил всё в казике» ↔ «казино его обнулило» — для FTS разные слова,
    # для cosine — соседи. Подключается прозрачно: при отсутствии embedding
    # у факта или embedder выключен — компонент даёт 0, общий ранкер работает
    # как и раньше. Cosine distance ∈ [0, 2] → сходство = 1 - dist/2 ∈ [0, 1].
    query_vec = await drun_embeddings.embed_text(session, query)
    has_vector = bool(
        await session.scalar(
            select(literal(1)).where(
                text("EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            )
        )
    )

    score = base_score + literal(6.0) * ts_rank
    if has_trgm:
        sim = func.similarity(AiMemory.fact, literal(query))
        score = score + literal(3.0) * sim
    if query_vec is not None and has_vector:
        # COALESCE: если у записи embedding пуст — вклад = 0 (не штрафуем).
        # `<=>` это cosine distance pgvector; чем меньше, тем ближе.
        qlit = drun_embeddings._vector_literal(query_vec)
        vec_sim = text(
            f"COALESCE(1 - (embedding <=> CAST('{qlit}' AS vector)) / 2.0, 0)"
        )
        # Вес 8 — самый сильный сигнал среди тематических компонентов:
        # семантика важнее, чем точное совпадение токенов.
        score = score + literal(8.0) * vec_sim

    # Жёсткое отсечение «совсем мимо»: либо FTS дал ненулевой ранк, либо
    # trigram-сходство выше шумового порога, либо vector-сходство значимое —
    # иначе кандидат не интересен. Векторный порог 0.75 (cosine sim) —
    # калибровка под text-embedding-3-small: ниже идёт шум по смыслу.
    relevance_conds = [ts_rank > 0.0]
    if has_trgm:
        relevance_conds.append(
            func.similarity(AiMemory.fact, literal(query)) > 0.15
        )
    if query_vec is not None and has_vector:
        qlit = drun_embeddings._vector_literal(query_vec)
        relevance_conds.append(
            text(
                f"embedding IS NOT NULL AND "
                f"(1 - (embedding <=> CAST('{qlit}' AS vector)) / 2.0) > 0.75"
            )
        )
    relevance_filter = or_(*relevance_conds)

    # Двухпроходная стратегия: сначала пробуем по теме, и если результатов мало —
    # добиваем по чистому base_score (вес × свежесть), как при пустом query.
    topic_stmt = (
        select(AiMemory)
        .where(and_(not_expired, scope, relevance_filter))
        .order_by(score.desc(), AiMemory.created_at.desc())
        .limit(limit)
    )
    topic_rows = (await session.execute(topic_stmt)).scalars().all()
    if len(topic_rows) >= limit:
        return list(topic_rows)

    # Добор. Исключаем уже выбранные id, чтобы не дублировать.
    taken_ids = [m.id for m in topic_rows]
    filler_stmt = (
        select(AiMemory)
        .where(and_(not_expired, scope))
        .where(~AiMemory.id.in_(taken_ids) if taken_ids else literal(True))
        .order_by(base_score.desc(), AiMemory.created_at.desc())
        .limit(limit - len(topic_rows))
    )
    filler_rows = (await session.execute(filler_stmt)).scalars().all()
    return list(topic_rows) + list(filler_rows)


async def _scored_memories_python(
    session: AsyncSession,
    *,
    scope,
    not_expired,
    query: str,
    limit: int,
    now: datetime,
) -> list[AiMemory]:
    """Старый Python-скоринг как fallback, если SQL-ранкинг недоступен."""
    rows = (
        await session.execute(
            select(AiMemory)
            .where(and_(not_expired, scope))
            .order_by(AiMemory.weight.desc(), AiMemory.created_at.desc())
            .limit(_CANDIDATE_POOL)
        )
    ).scalars().all()
    if not rows:
        return []
    query_tokens = _tokenize(query) if query else set()
    ranked = sorted(
        rows, key=lambda m: _score_memory(m, query_tokens, now), reverse=True
    )
    return ranked[:limit]
