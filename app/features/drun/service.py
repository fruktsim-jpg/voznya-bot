"""Ядро Тёмного друна: генерация реплик с подмешиванием контекста и памятью.

Поток ``generate``:
1. читаем конфиг (``ai_settings``); если не usable — молчим;
2. собираем system prompt (персона + мир + правила);
3. собираем контекст (статистика игрока, сезон, события, чат, память);
4. подмешиваем краткосрочную историю канала;
5. зовём провайдера; чистим вывод фильтром (анти-официоз);
6. пишем запрос+ответ в ``ai_messages`` (краткосрочная память).

Никаких записей в экономику. Любая ошибка модели → ``GenerateResult(ok=False)``,
бот это переживает (друн просто промолчал).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.features.drun import config as drun_config
from app.features.drun import context as drun_context
from app.features.drun import actions as drun_actions
from app.features.drun import ask as drun_ask
from app.features.drun import emotion as drun_emotion
from app.features.drun import filter as drun_filter
from app.features.drun import memory as drun_memory
from app.features.drun import persona as drun_persona
from app.features.drun import policy as drun_policy
from app.features.drun import provider as drun_provider
from app.features.drun import response_mode as drun_response_mode
from app.features.drun import variance as drun_variance

logger = get_logger(__name__)


async def _heal_if_poisoned(session: AsyncSession) -> bool:
    """Чинит сессию, отравленную аборченной транзакцией upstream-кодом.

    INCIDENT 2026-06-18: upstream-middleware (user_tracking, achievements,
    moderation backstop и т.д.) делают SQL в общей сессии и ловят все
    Exception без rollback. Если их SQL уронит asyncpg-транзакцию (любая
    DBAPIError), без rollback она уходит в `InFailedSQLTransactionError` —
    и ВСЁ дальнейшее, включая `SAVEPOINT sa_savepoint_N`, валится с
    «current transaction is aborted». Savepoint не спасает: SAVEPOINT — это
    тоже SQL, и его prepare уже падает на отравленной транзакции.

    Этот хелпер пробует дешёвый ``SELECT 1`` через savepoint: если он
    проходит — транзакция жива; если падает с InFailedSQLTransactionError
    — делаем ``session.rollback()``, чтобы вернуть сессию в рабочее
    состояние. Возвращает True, если пришлось лечить (для лога).

    Цена починки: теряем неподтверждённые upstream-записи этой сессии
    (user_tracking.upsert_user, ears.capture_chat и т.п.). Это
    допустимая жертва: они и так не доехали бы до commit, а альтернатива
    — крах всего чат-хэндлера.
    """
    from sqlalchemy import text as sql_text

    try:
        async with session.begin_nested():
            await session.execute(sql_text("SELECT 1"))
        return False
    except DBAPIError as exc:
        msg = str(exc).lower()
        if "aborted" in msg or "infailedsqltransactionerror" in msg:
            logger.warning(
                "drun: outer transaction poisoned upstream — healing via rollback",
                exc_info=False,
            )
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001
                logger.warning("drun: rollback after poison failed", exc_info=True)
            return True
        raise

_DEFAULT_OBSERVATION = (
    "Вкинь в чат короткую живую реплику — глянь о чём базарят и вцепись в тему "
    "или вкинь мем. 1-2 фразы, без статистики и пересказа событий. Хороший "
    "вкид цепляется за конкретного человека, старую память, конфликт, тишину "
    "или легенду. Если зацепки нет — лучше молчи, чем лей универсальный шум."
)
_DEFAULT_REACTION = (
    "Среагируй одной живой репликой на то, что в чате/мире — коротко, в тему, "
    "по настроению. Без лент и списков. Не пересказывай событие как диктор: "
    "добавь отношение, кому это припомнить, кто красавчик или кто опозорился."
)
_DEFAULT_REPLY = (
    "К ТЕБЕ ЛИЧНО ОБРАТИЛСЯ ЧЕЛОВЕК. Его сообщение — в самом низу, в блоке "
    "«СООБЩЕНИЕ ДЛЯ ТЕБЯ», там же его ИМЯ. Твоя задача №1 — ОТВЕТИТЬ ИМЕННО "
    "ЭТОМУ ЧЕЛОВЕКУ на ЭТО сообщение, как в живом диалоге.\n"
    "- В чате пишут РАЗНЫЕ люди. В истории у каждой реплики есть имя автора, а "
    "у твоих прошлых ответов — пометка, КОМУ ты отвечал. НЕ путай людей: если "
    "тебя оскорбил один, а сейчас спрашивает ДРУГОЙ — отвечай тому, кто "
    "написал СЕЙЧАС, и не переноси на него чужие слова/обиды.\n"
    "- Если спросил «как дела» — скажи как у тебя дела (дерзко, в образе).\n"
    "- Если сказал «люблю тебя» — отреагируй на это, а не на посторонних.\n"
    "- Если задал вопрос — ОТВЕТЬ на вопрос.\n"
    "ЖИВОЙ ЧАТ выше — это ТОЛЬКО фон для настроения. НЕ пересказывай его, НЕ "
    "комментируй посторонних, если человек спросил не про них. "
    "Отвечай НА РЕПЛИКУ ЧЕЛОВЕКА, обращайся к НЕМУ по имени. Подстраивай тон "
    "под него и его состояние (см. правила режимов). Приоритет: сначала прямой "
    "смысл сообщения, потом личная память/отношение/реплай-нить, и только потом "
    "статы/ешки, если они реально в тему. Если есть конкретная память о человеке "
    "— используй максимум одну деталь как живой укол/намёк, не пересказывай "
    "досье. Не ассистент, но и не мимо темы."
)

_GRANT_CLAIM_RE = re.compile(
    r"\b(накину|накинул|начисл(?:ю|ил|ила|ено)|выдам|выдал|выдала|держи\s+\d*\s*еш\w*|"
    r"держи\s+еш\w*|подар(?:ю|ил|ила).*еш\w*|подкин(?:у|ул|ула).*еш\w*|сжал(?:юсь|ился|илась))\b",
    re.IGNORECASE,
)
_TAX_CLAIM_RE = re.compile(
    r"\b(спиш(?:у|у-ка|ал|ала)|снял|сняла|сниму|оштраф(?:ую|овал|овала)|"
    r"налог|плати\s+налог|отбер(?:у|у-ка|ал|ала).*еш\w*)\b",
    re.IGNORECASE,
)


def _money_claim_kind(text: str) -> str | None:
    """Определяет, утверждает ли видимый текст реальное движение ешек."""
    if not text:
        return None
    grant = bool(_GRANT_CLAIM_RE.search(text))
    tax = bool(_TAX_CLAIM_RE.search(text))
    if grant and not tax:
        return "grant"
    if tax and not grant:
        return "tax"
    if grant or tax:
        return "money"
    return None


def _guard_econ_claim(text: str, econ_result: object | None) -> str:
    """Не даёт Друну публично соврать про деньги, если операция не прошла.

    Промпт просит модель ставить ``[[econ:...]]`` рядом с такими словами, но
    LLM иногда обещает «накину/сниму» без директивы или при заблокированной
    операции. Вместо молчаливой лжи добавляем короткую честную поправку.
    """
    claim = _money_claim_kind(text)
    if claim is None:
        return text
    ok = bool(getattr(econ_result, "ok", False))
    kind = str(getattr(econ_result, "kind", "") or "")
    if ok and (claim == "money" or claim == kind):
        return text
    reason = str(getattr(econ_result, "reason", "") or "no_directive")
    if reason == "no_directive":
        correction = "ешки реально не трогал — это был трёп, не бухгалтерия"
    else:
        correction = f"ешки реально не тронул: система не дала ({reason})"
    return f"{text.rstrip()}\n\n({correction})"


# --- Связь восприятия с экономической властью --------------------------------
# Карта intent → подсказка модели «можешь применить ешко-выходку». Это НЕ
# приказ: модель сама решает, вставлять директиву ``[[econ:...]]`` или нет.
# Подсказка появляется в задании только при включённом ``econ_enabled``; вся
# фактическая защита (cap, clamp, cooldown, daily_cap, self-grant block) сидит
# в econ.apply и сработает даже если модель попросит что-то безумное.
_ECON_HINT_BY_INTENT: dict[str, str] = {
    "roast": (
        "собеседник нарывается/хвастается/наезжает — если уместно, можешь "
        "вкатить налог: вставь на отдельной строке "
        "`[[econ:tax:N:за что]]`, где N — небольшая сумма ешек."
    ),
    "hype": (
        "крупный куш/иксы у собеседника — если в образе уместно, можешь "
        "накинуть сверху подачку как ведущий движа: вставь на отдельной "
        "строке `[[econ:grant:N:за что]]`."
    ),
    "support": (
        "человек скис/жалуется — РЕДКО, если в образе, можешь кинуть "
        "копеечную подачку из жалости: `[[econ:grant:N:по жалости]]`."
    ),
}


def _econ_hint_for_intent(intent_kind: str | None) -> str:
    """Возвращает подсказку для модели по эконом-выходке (или пусто)."""
    if not intent_kind:
        return ""
    return _ECON_HINT_BY_INTENT.get(intent_kind.lower(), "")


async def _peek_mood(session: AsyncSession, channel: str) -> tuple[str | None, int]:
    """Мгновенное настроение мира (mood) для смещения стиля. Сбой → (None, 1).

    Изолируем в savepoint: mood делает SELECT'ы, и на отравленной транзакции
    они бы каскадно валили generate. begin_nested откатывает только себя.
    """
    try:
        from app.features.drun import mood as drun_mood

        async with session.begin_nested():
            m = await drun_mood.compute_mood(session, channel=channel)
        return m.label, int(m.intensity or 1)
    except Exception:  # noqa: BLE001
        logger.debug("peek_mood failed", exc_info=True)
        return None, 1


async def _peek_emotion(session: AsyncSession):
    """Стойкое настроение друна (emotion) с учётом затухания. Сбой → нейтрал."""
    try:
        async with session.begin_nested():
            return await drun_emotion.get_state(session)
    except Exception:  # noqa: BLE001
        logger.debug("peek_emotion failed", exc_info=True)
        return drun_emotion.Emotion(0.0, 0.0)


async def _peek_affinity(session: AsyncSession, subject_id: int | None) -> int:
    """Накопленное личное отношение к собеседнику (-100..100). Сбой → 0."""
    if subject_id is None:
        return 0
    try:
        from app.features.drun import affinity as drun_affinity

        async with session.begin_nested():
            aff = await drun_affinity.get_affinity(session, subject_id)
        return int(getattr(aff, "score", 0) or 0)
    except Exception:  # noqa: BLE001
        logger.debug("peek_affinity failed", exc_info=True)
        return 0


async def _peek_opinion(session: AsyncSession, subject_id: int | None):
    """Сложившееся многомерное мнение об игроке (для окраски стиля). Сбой → нейтрал.

    Возвращает :class:`opinions.Opinion` (с осями 0..100). Изолируем в savepoint:
    ходит в БД, на отравленной транзакции иначе уронил бы generate.
    """
    from app.features.drun import opinions as drun_opinions

    if subject_id is None:
        return drun_opinions.neutral()
    try:
        async with session.begin_nested():
            return await drun_opinions.get_opinion(session, subject_id)
    except Exception:  # noqa: BLE001
        logger.debug("peek_opinion failed", exc_info=True)
        return drun_opinions.neutral()


@dataclass
class GenerateResult:
    """Результат генерации реплики друна."""

    ok: bool
    text: str = ""
    error: str = ""
    econ: object | None = None  # econ.EconResult, если друн применил налог/подачку


async def generate(
    session: AsyncSession,
    *,
    task: str,
    subject_id: int | None = None,
    subject_name: str | None = None,
    channel: str = "chat",
    include_events: bool = True,
    include_chat: bool = True,
    chat_limit: int = 24,
    trigger_event_id: int | None = None,
    remember_message: bool = True,
    memory_user_content: str | None = None,
    memory_kind: str = "monologue",
    allow_actions: bool = False,
    role: str | None = None,
    query: str | None = None,
    intent_kind: str | None = None,
    asker_id: int | None = None,
    addressed: bool = True,
    urge: float = 0.0,
    vary: bool = True,
    grounded: bool = False,
) -> GenerateResult:
    """Генерирует одну реплику друна под конкретное задание ``task``.

    ``memory_user_content`` — что СОХРАНИТЬ в краткосрочную историю как реплику
    пользователя (по умолчанию = ``task``). Для ответов игроку сюда кладут
    чистую реплику человека, а не громоздкий шаблон-промпт, чтобы история
    диалога оставалась читаемой и не раздувала контекст инструкциями.

    ``memory_kind`` — тип хода: ``reply`` (реальный диалог человек↔друн,
    участвует в истории) или ``monologue`` (автономный вкид/реакция — НЕ
    подмешивается в историю, чтобы друн не продолжал свою же ленту рофлов
    вместо ответа на короткий вопрос).

    ``vary`` — включает слой ВАРИАТИВНОСТИ (:mod:`variance`): на каждый ответ
    собирается уникальный стиль (длина/яд/агрессия/тепло) + override температуры,
    чтобы реплики не схлопывались к «средне-дерзкой реплике средней длины». Для
    служебных объявлений (announce) можно отключить.

    ``grounded`` — режим ответа по внешним/проверенным фактам (web/ask). Он не
    выключает голос друна, но ограничивает температуру, чтобы модель меньше
    додумывала поверх найденных данных.
    """
    # Лечим отравленную upstream-кодом транзакцию ДО любой SQL-операции.
    # См. docstring _heal_if_poisoned: без этого даже первый get_config()
    # либо наша savepoint-обёртка ниже валится на самом
    # `SAVEPOINT ...`-стейтменте с «current transaction is aborted».
    await _heal_if_poisoned(session)

    cfg = await drun_config.get_config(session)
    pol = await drun_policy.build_policy(
        session,
        subject_id=subject_id,
        channel=channel,
        intent_kind=intent_kind,
        addressed=addressed,
        text=query or memory_user_content or task,
    )
    policy_block = pol.block()
    if policy_block:
        task = f"{policy_block}\n\n{task}"
    if not cfg.usable:
        return GenerateResult(ok=False, error="disabled")

    # ИЗОЛЯЦИЯ ФАЗЫ СБОРКИ ПРОМПТА (INCIDENT 2026-06-18 part 4).
    #
    # Раньше тут стоял ОДИН savepoint вокруг всей фазы (build_system_prompt +
    # build_context). Он НЕ работал, потому что 28+ best-effort блоков в
    # context.py/persona.py сами ловят Exception без rollback'а savepoint'а —
    # отравленная asyncpg-транзакция доезжает до RELEASE SAVEPOINT и валит
    # хэндлер целиком (см. инцидент с fastembed: модель e5-small исчезла из
    # библиотеки → embedder бросал ValueError из memory_block → транзакция
    # отравлялась → recent_messages и add_message падали → друн молчал).
    #
    # Теперь правильный уровень изоляции — внутри самих блоков (helper
    # ``context._isolated``: каждый блок открывает свой savepoint и при
    # любом исключении делает ROLLBACK TO SAVEPOINT). Здесь же оставляем
    # только внешний guard: если по какой-то причине внешняя транзакция
    # всё-таки повредилась (например, persona.build_system_prompt уронил
    # session ещё ДО build_context), лечим её и продолжаем с минимальным
    # system'ом — это лучше, чем уронить ответ.
    system = ""
    ctx = ""
    try:
        system = await drun_persona.build_system_prompt(
            session, econ_enabled=(allow_actions and cfg.econ_enabled)
        )
    except Exception:  # noqa: BLE001
        logger.warning("drun build_system_prompt failed", exc_info=True)
        await _heal_if_poisoned(session)
        system = "Ты — Тёмный друн, смотритель чата. Отвечай коротко и в образе."
    try:
        ctx = await drun_context.build_context(
            session,
            subject_id=subject_id,
            include_events=include_events,
            channel=channel,
            include_chat=include_chat,
            chat_limit=chat_limit,
            query=query,
        )
    except Exception:  # noqa: BLE001
        logger.warning("drun build_context failed", exc_info=True)
        await _heal_if_poisoned(session)
        ctx = ""
    memory_ids = list(getattr(ctx, "memory_ids", []) or [])
    archive_ids = list(getattr(ctx, "archive_ids", []) or [])
    # Страховка перед фазой записи: если что-то в helpers всё же отравило
    # транзакцию (например, асинхронный таск, или persona без savepoint'а),
    # лечим до recent_messages/add_message.
    await _heal_if_poisoned(session)

    # SAVEPOINT и здесь: recent_messages — критический путь истории, но
    # сама по себе одна SELECT; если БД временно недоступна или таблица
    # повреждена, лучше ответить «без памяти», чем уронить весь хэндлер
    # (см. INCIDENT 2026-06-18 part 3).
    history: list = []
    try:
        async with session.begin_nested():
            history = list(
                await drun_memory.recent_messages(session, channel=channel, limit=10)
            )
    except Exception:  # noqa: BLE001
        logger.warning("drun recent_messages failed, continuing without history", exc_info=True)
        history = []
    messages: list[dict[str, str]] = []
    for m in history:
        if m.role == "user":
            # У user-хода в content уже есть префикс «Имя: текст» (см.
            # memory_user_content). Оставляем как есть — модель видит автора.
            messages.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            # Помечаем, КОМУ друн отвечал в этом ходе: в диалоге участвуют
            # РАЗНЫЕ люди, и без адресата модель путает контексты («сказал
            # Васе „иди спать“» → новому собеседнику «ты же спать ушёл»).
            # Тег служебный, в квадратных скобках; если модель его скопирует в
            # видимый ответ — срежет пост-фильтр (см. filter.clean).
            to_name = (m.meta or {}).get("to_name")
            content = (
                f"[ты отвечал {to_name}]: {m.content}"
                if to_name else m.content
            )
            messages.append({"role": "assistant", "content": content})
    # СЛОЙ ВАРИАТИВНОСТИ + СТОЙКОЕ НАСТРОЕНИЕ. Это лекарство от однообразия:
    # вместо статичной инструкции «будь разным» (которая всегда регрессирует к
    # среднему) собираем КОНКРЕТНУЮ разнарядку на ЭТУ реплику — длину, яд,
    # агрессию, тепло — со смещённым рандомом, плюс override температуры. Сигналы
    # смещения: намерение perceive, мгновенное настроение мира (mood), стойкая
    # эмоция друна (emotion), накопленное аффинити к собеседнику. Любой сбой —
    # деградируем к дефолтному поведению, ответ не роняем.
    style = None
    temp_override: float | None = None
    if vary:
        try:
            mood_label, mood_intensity = await _peek_mood(session, channel)
            emo = await _peek_emotion(session)
            affinity_score = await _peek_affinity(session, subject_id)
            op = await _peek_opinion(session, subject_id)
            # Стойкая эмоция двигает базовое настроение для стиля: взвинченность
            # подмешивает «angry/chaotic»-смещение даже без свежих событий мира.
            eff_mood = mood_label
            if emo.arousal >= 0.6 and emo.valence <= -0.2:
                eff_mood = "angry"
            elif emo.arousal >= 0.6 and emo.valence >= 0.2:
                eff_mood = "excited"
            style = drun_variance.build_style(
                intent_kind=intent_kind,
                mood_label=eff_mood,
                mood_intensity=mood_intensity,
                affinity_score=affinity_score,
                urge=urge,
                addressed=addressed,
                base_temperature=cfg.temperature,
                op_annoyance=op.get("annoyance"),
                op_respect=op.get("respect"),
                op_entertainment=op.get("entertainment"),
                op_trust=op.get("trust"),
            )
            temp_override = style.temperature
            # Директивы стиля и стойкого настроения — в самый конец задания,
            # ближе всего к точке генерации (модель сильнее слушает финал).
            extra = [style.directive()]
            emo_dir = emo.directive()
            if emo_dir:
                extra.append(emo_dir)
            task = task + "\n\n" + "\n\n".join(extra)
        except Exception:  # noqa: BLE001
            logger.debug("drun variance/emotion failed, using defaults", exc_info=True)
            style = None
            temp_override = None

    # РЕЖИМ ОТВЕТА (детерминированный): вопрос/гайд/психолог/наезд/прикол/смолток.
    # Persona-промпт описывает режимы абстрактно и тонет в общем блоке — модель
    # сваливается в один токсичный тон и иногда не отвечает на прямой вопрос.
    # Здесь по последней реплике человека подмешиваем ОДНУ короткую директиву в
    # самый конец задания (recency: модель сильнее слушает финал). Только для
    # адресованных реплик с реальным текстом человека.
    mode_source = (query or "").strip() or (
        memory_user_content or "" if memory_kind == "reply" else ""
    ).strip()
    if addressed and mode_source:
        try:
            _mode_name, mode_block = drun_response_mode.mode_directive(
                mode_source, addressed=addressed
            )
            task = task + "\n\n" + mode_block
        except Exception:  # noqa: BLE001
            logger.debug("drun response_mode failed, skipping", exc_info=True)

    if grounded:
        # Для web/factual ответов стиль может быть живым, но декодер — холоднее:
        # свежие данные важнее импровизации. Это снижает «додумывание» поверх
        # найденных страниц/погоды/курсов без отдельной модели.
        temp_override = min(temp_override if temp_override is not None else cfg.temperature, 0.45)

    user_content = (f"{ctx}\n\n# ЗАДАНИЕ\n{task}" if ctx else task).strip()
    messages.append({"role": "user", "content": user_content})

    try:
        raw = await drun_provider.chat(
            cfg, system=system, messages=messages,
            model=cfg.model_for(role or drun_config.ROLE_NARRATOR),
            temperature=temp_override,
        )
    except drun_provider.LlmError as exc:
        logger.warning("drun generate failed: %s", exc)
        return GenerateResult(ok=False, error=str(exc))

    # Потолок длины под выбранную ось длины (на TERSE модель часто игнорит
    # словесную рамку — режем жёстко пост-фильтром).
    max_chars = style.max_chars if style is not None else 1200
    text = drun_filter.clean(raw, max_chars=max_chars)
    if not text:
        return GenerateResult(ok=False, error="empty")

    # СВЕРКА ФАКТОВ (read-директивы [[ask:...]]). Если в черновике друн
    # попросил данные из БД (баланс/ранг/топ/досье третьего лица), резолвим их
    # и делаем РОВНО ОДИН доп. вызов с подмешанными фактами. Большинство реплик
    # директив не содержит → лишних вызовов нет. Параллель с econ-директивами,
    # но это ТОЛЬКО ЧТЕНИЕ. Изоляция savepoint'ами — внутри ask.resolve.
    if drun_ask.has_directive(text):
        facts = ""
        try:
            facts = await drun_ask.resolve(session, text)
        except Exception:  # noqa: BLE001
            logger.warning("drun ask.resolve failed", exc_info=True)
        # Безусловно лечим транзакцию после сверки. Хелперы tools_read глотают
        # свои SQL-ошибки внутри, поэтому savepoint в ask.resolve может не
        # откатиться, а RELEASE на отравленной транзакции тихо её не починит
        # (INCIDENT 2026-06-18 part 4). Без этого heal отравленная транзакция
        # доехала бы до econ.apply/add_message ниже и уронила хэндлер.
        await _heal_if_poisoned(session)
        if facts:
            # Чистим черновик от директив, отдаём модели факты и просим
            # финальную реплику. messages уже содержит контекст+задание; добавляем
            # факты отдельным user-ходом, чтобы модель видела сверку как новое
            # входное знание, а не как часть прошлого задания.
            followup = list(messages)
            followup.append({"role": "assistant", "content": drun_ask.strip_directives(text)})
            followup.append({
                "role": "user",
                "content": (
                    f"{facts}\n\n# Теперь дай финальный ответ, опираясь на эти "
                    f"ПРОВЕРЕННЫЕ факты. Не выдумывай цифры. Без директив."
                ),
            })
            try:
                raw2 = await drun_provider.chat(
                    cfg, system=system, messages=followup,
                    model=cfg.model_for(role or drun_config.ROLE_NARRATOR),
                    temperature=temp_override,
                )
                text2 = drun_filter.clean(raw2, max_chars=max_chars)
                if text2:
                    text = text2
            except drun_provider.LlmError as exc:
                logger.warning("drun ask follow-up failed: %s", exc)
        # В любом случае директивы не должны утечь в видимый текст.
        text = drun_ask.strip_directives(text) or "..."

    # Экономическая выходка (налог/подачка), если друн вставил директиву и
    # власть включена. Применяем к субъекту реплики.
    #
    # ``asker_id`` отделён от ``subject_id`` намеренно. ``None`` означает, что
    # это инициатива друна: grant адресату разрешён и проходит через обычные
    # cap/cooldown/daily-cap лимиты. Если caller явно передаст asker_id ==
    # target_id, econ.apply заблокирует self-grant. Пользовательские директивы
    # заранее калечатся sanitize_user_text(), поэтому обычный respond() может
    # безопасно оставлять asker_id=None: деньги двигает только директива модели,
    # не отражённый ввод игрока.
    econ_result = None
    if allow_actions and cfg.econ_enabled and pol.allow_econ_hint:
        econ_result = await drun_actions.apply_if_any(
            session, cfg=cfg, target_id=subject_id, text=text,
            asker_id=asker_id, intent_kind=intent_kind,
        )
    if drun_actions.parse(text) is not None:
        text = drun_actions.strip_directives(text)
        if not text:
            text = "..."
    text = _guard_econ_claim(text, econ_result)

    if remember_message:
        # В историю кладём чистую реплику (а не весь шаблон), чтобы диалог
        # читался по-человечески и не засорял контекст инструкциями. Тип хода
        # (reply/monologue) — в meta, чтобы автономные вкиды не подмешивались
        # в историю диалога.
        await drun_memory.add_message(
            session,
            role="user",
            content=memory_user_content or task,
            channel=channel,
            user_id=subject_id,
            trigger_event_id=trigger_event_id,
            meta={"kind": memory_kind},
        )
        await drun_memory.add_message(
            session,
            role="assistant",
            content=text,
            channel=channel,
            user_id=subject_id,
            trigger_event_id=trigger_event_id,
            meta={
                "kind": memory_kind,
                "to_name": subject_name,
                "memory_ids": memory_ids[:32],
                "archive_ids": archive_ids[:32],
            },
        )

    return GenerateResult(ok=True, text=text, econ=econ_result)


async def observe(
    session: AsyncSession,
    *,
    subject_id: int | None = None,
    channel: str = "chat",
    intent_note: str | None = None,
    intent_kind: str | None = None,
    urge: float = 0.0,
) -> GenerateResult:
    """Спонтанное встревание друна в живой чат (не ответ на обращение).

    ``intent_note`` — направляющая от слоя восприятия (perceive): ЗАЧЕМ друн
    влезает (подколоть/поддержать/подлить движа/...). Без неё это просто живой
    вкид по настроению чата. С ней — целенаправленное социальное действие,
    что и делает друна агентом, а не генератором случайных реплик.

    ``intent_kind`` — машинный код интента (roast/hype/...); пробрасывается
    дальше в эконом-выходку как audit trail (см. econ.apply meta).

    Эконом-власть в этом пути ВКЛЮЧЕНА (LEAP-2): спонтанный ROAST на хвастуна
    или HYPE на победителя — самый естественный момент для ``drun_tax``/
    ``drun_grant``. Подсказка модели появляется только при включённой власти и
    подходящем интенте; все предохранители econ.apply (cap/clamp/cooldown/
    daily_cap) работают. ``asker_id=None`` явно: игрок ничего не «просил» —
    это инициатива друна, поэтому self-grant-блок здесь не применим.
    """
    task = await drun_config.get_prompt(
        session, drun_config.PROMPT_OBSERVATION, _DEFAULT_OBSERVATION
    )
    cfg = await drun_config.get_config(session)
    pol = await drun_policy.build_policy(
        session,
        subject_id=subject_id,
        channel=channel,
        intent_kind=intent_kind,
        addressed=False,
    )
    if intent_kind and cfg.econ_enabled and pol.allow_econ_hint and subject_id is not None:
        hint = _econ_hint_for_intent(intent_kind)
        if hint:
            task = f"{task}\n\n# ВОЗМОЖНОЕ ДЕЙСТВИЕ: {hint}"
    if intent_note:
        task = (
            f"{task}\n\n# ТВОЁ НАМЕРЕНИЕ СЕЙЧАС (действуй по нему): {intent_note}"
        )
    # ``allow_actions=True`` ТОЛЬКО когда есть конкретный субъект — иначе
    # некого облагать/одаривать. ``asker_id=None`` (см. docstring).
    return await generate(
        session, task=task, subject_id=subject_id, channel=channel,
        intent_kind=intent_kind,
        allow_actions=(subject_id is not None),
        asker_id=None,
        addressed=False,
        urge=urge,
    )


async def react(
    session: AsyncSession,
    *,
    trigger_event_id: int | None = None,
    subject_id: int | None = None,
    channel: str = "chat",
) -> GenerateResult:
    """Реакция на свежие события мира (V1)."""
    task = await drun_config.get_prompt(
        session, drun_config.PROMPT_REACTION, _DEFAULT_REACTION
    )
    return await generate(
        session,
        task=task,
        subject_id=subject_id,
        channel=channel,
        trigger_event_id=trigger_event_id,
    )


async def respond(
    session: AsyncSession,
    *,
    asker_id: int,
    asker_name: str,
    text: str,
    channel: str = "chat",
    reply_context: str | None = None,
    intent_note: str | None = None,
    intent_kind: str | None = None,
    urge: float = 0.5,
) -> GenerateResult:
    """Ответ друна на обращение игрока в чате (по контексту беседы).

    ``reply_context`` — если игрок отвечает реплаем на КОНКРЕТНОЕ сообщение
    (его собственную реплику друна или чужую), сюда кладётся её текст. Без
    этого друн «отвечает в пустоту» и теряет нить: человек реагирует на
    конкретную фразу, а друн видел только новый текст.

    ``intent_note`` / ``intent_kind`` — направляющая от слоя восприятия
    (perceive): зачем встревать (ROAST/HYPE/SUPPORT/...). Это и тон ответа,
    и СИГНАЛ к эконом-выходке: при ROAST/HYPE/BRAG модель уведомляется, что
    может (опционально) вставить ``[[econ:tax/grant:N:за что]]``. Сама
    директива остаётся опциональной; предохранители econ.apply (cap, clamp,
    cooldown, daily_cap, self-grant block) гарантируют безопасность.
    """
    template = await drun_config.get_prompt(
        session, drun_config.PROMPT_REPLY, _DEFAULT_REPLY
    )
    # Обезвреживаем econ-директивы в НЕДОВЕРЕННОМ вводе игрока: иначе игрок мог
    # бы написать [[econ:grant:1000:...]] и через эхо модели спровоцировать
    # самоначисление. Чистим до отправки в LLM и до сохранения в память.
    safe_text = drun_actions.sanitize_user_text(text.strip())
    safe_reply_ctx = (
        drun_actions.sanitize_user_text(reply_context.strip())[:400]
        if reply_context else ""
    )
    # Личное отношение (аффинити): обновляем по тону реплики игрока В АДРЕС
    # друна. Тёплое общение копит дружбу, хамство — вражду; со временем
    # затухает. Дёшево, без LLM. Коммит — в общем потоке generate ниже.
    #
    # ИЗОЛЯЦИЯ ТРАНЗАКЦИИ (INCIDENT 2026-06-18): три best-effort блока
    # ниже (affinity / websearch / governor) делают SQL в общей сессии и
    # ловят все Exception. Если их SQL уронит сессию (DBAPIError любой
    # природы — schema drift, FK, deadlock, прерванная коннекция), то без
    # rollback СЛЕДУЮЩИЙ SELECT в generate() падает с
    # InFailedSQLTransactionError, и весь хэндлер дохнет. Чтобы изолировать
    # такие сбои на уровне одного помощника, оборачиваем каждый в
    # SAVEPOINT через session.begin_nested(): провал внутри роллбэчит
    # ТОЛЬКО savepoint, внешняя транзакция остаётся чистой, generate
    # продолжает работать (просто без вклада от упавшего блока).
    #
    # Уровень лога подняли до WARNING: тихие debug-сообщения скрыли
    # реальную причину аварии 18.06.2026. Теперь любой сбой виден сразу.
    try:
        from app.features.drun import affinity as drun_affinity

        async with session.begin_nested():
            tone_sent, tone_gist, prev_aff = await drun_affinity.record_interaction(
                session, asker_id, safe_text
            )
        # ПАМЯТНЫЙ ЭПИЗОД В АДРЕС ДРУНА (LEAP-5): сильный тон к нему лично — это
        # не просто сдвиг аффинити, а ПОСТУПОК, который друн запомнит как момент.
        # Резкий наезд → «унижение/нытьё» в его адрес; явное тепло после вражды
        # → примирение; обычное тепло → поддержка. Берём только ЯРКИЙ тон
        # (|sentiment|>=2) с непустым gist, чтобы не плодить эпизоды на «спс».
        try:
            if tone_gist and abs(tone_sent) >= 2:
                async with session.begin_nested():
                    from app.features.drun import episodes as drun_episodes

                    if tone_sent <= -2:
                        code = "humiliation"
                    elif prev_aff <= -25:
                        code = "reconciliation"  # был врагом → вдруг тепло
                    else:
                        code = "support"
                    await drun_episodes.record_episode(
                        session, subject_id=asker_id, code=code,
                        gist=f"(в твой адрес) {tone_gist}"[:200], significance=2,
                    )
        except Exception:  # noqa: BLE001
            logger.debug("respond personal episode failed", exc_info=True)
    except Exception:  # noqa: BLE001
        logger.warning("respond affinity update failed", exc_info=True)
    # Стойкое НАСТРОЕНИЕ друна: тон обращения в его адрес сдвигает эмоцию,
    # которая ПЕРЕЖИВЁТ этот ответ и покрасит следующие (взвинтят наездом —
    # друн ещё какое-то время резче со всеми). Дёшево, без LLM; затухает само.
    try:
        async with session.begin_nested():
            if intent_kind == "roast":
                await drun_emotion.apply_nudge(session, drun_emotion.NUDGE_HOSTILE)
            elif intent_kind == "support":
                await drun_emotion.apply_nudge(session, drun_emotion.NUDGE_WARM)
            elif intent_kind == "hype":
                await drun_emotion.apply_nudge(session, drun_emotion.NUDGE_WIN)
    except Exception:  # noqa: BLE001
        logger.debug("respond emotion nudge failed", exc_info=True)
    # Фактический вопрос (погода/новости/курс/«что такое») → подтягиваем свежие
    # данные из интернета, чтобы друн не выдумывал. Для внутренних тем Возни и
    # обычной болтовни веб не дёргается (см. websearch.looks_factual).
    web_block = ""
    try:
        from app.features.drun import websearch as drun_web

        async with session.begin_nested():
            web_block = await drun_web.auto_context(session, safe_text)
    except Exception:  # noqa: BLE001
        logger.warning("respond web auto_context failed", exc_info=True)
    # «Чувство комнаты»: если чат сейчас абузит бота (каждый второй на нём
    # висит) — подмешиваем установку отвечать короче/суше и переводить движ на
    # общение людей. В обычном режиме подсказка пустая и ничего не меняет.
    room_block = ""
    try:
        from app.features.drun import governor as drun_governor

        async with session.begin_nested():
            verdict = await drun_governor.assess(session, channel=channel)
        if verdict.throttle and verdict.note:
            room_block = f"# РЕЖИМ КОМНАТЫ: {verdict.note}"
    except Exception:  # noqa: BLE001
        logger.warning("respond governor assess failed", exc_info=True)
    # Реплай на конкретное сообщение: даём друну то, НА ЧТО именно отвечают.
    reply_line = ""
    if safe_reply_ctx:
        reply_line = (
            f"# ЭТО РЕПЛАЙ НА СООБЩЕНИЕ: «{safe_reply_ctx}»\n"
            f"# (человек отвечает ИМЕННО на эту фразу — учитывай её как нить)\n"
        )
    task = (
        f"{template}\n\n"
        f"========================\n"
        f"{reply_line}"
        f"# СООБЩЕНИЕ ДЛЯ ТЕБЯ от {asker_name} (ответь именно на него):\n"
        f"«{safe_text}»\n"
        f"========================"
    )
    # Подсказка по эконом-выходке: ROAST/HYPE-сигналы — это повод (но не
    # обязанность) применить налог/подачку. Директиву ``[[econ:...]]``
    # модель вставляет сама, если в реплике это уместно; иначе просто
    # отвечает тоном. Подсказку даём ТОЛЬКО при включённой власти, чтобы
    # модель не училась выдумывать директивы вхолостую.
    # cfg здесь подтягиваем отдельно: respond() сам по себе LLM не вызывает
    # (это делает generate ниже), но econ-флаг нужен ДО формирования task —
    # без него NameError валит весь хэндлер и аборт транзакции каскадирует
    # на следующие селекты (см. INCIDENT 2026-06-18).
    cfg = await drun_config.get_config(session)
    pol = await drun_policy.build_policy(
        session,
        subject_id=asker_id,
        channel=channel,
        intent_kind=intent_kind,
        addressed=True,
        text=safe_text,
    )
    if intent_kind and cfg.econ_enabled and pol.allow_econ_hint:
        hint = _econ_hint_for_intent(intent_kind)
        if hint:
            task = f"{task}\n\n# ВОЗМОЖНОЕ ДЕЙСТВИЕ: {hint}"
    if intent_note:
        task = f"{task}\n\n# ТОН/НАМЕРЕНИЕ: {intent_note}"
    if room_block:
        task = f"{room_block}\n\n{task}"
    if web_block:
        task = f"{web_block}\n\n{task}"
    return await generate(
        session,
        task=task,
        subject_id=asker_id,
        subject_name=asker_name,
        channel=channel,
        chat_limit=16,
        memory_user_content=f"{asker_name}: {safe_text}",
        memory_kind="reply",
        allow_actions=True,
        query=safe_text,
        intent_kind=intent_kind,
        addressed=True,
        urge=urge,
        grounded=bool(web_block),
    )


async def announce_action(
    session: AsyncSession,
    *,
    owner_name: str,
    command_text: str,
    result_summary: str,
    ok: bool,
    channel: str = "chat",
) -> GenerateResult:
    """Друн объявляет в чате результат owner-команды в своём образе.

    Это не болтовня и не выдумка: фактический итог (``result_summary``) уже
    посчитан инструментом. Друн лишь подаёт его эффектно, как ведущий движа.
    """
    if ok:
        task = (
            "Ты — Меллстрой, ведущий движа. Владелец только что провернул через "
            "тебя действие в чате, и оно УЖЕ ВЫПОЛНЕНО. Объяви результат залу "
            "коротко (1-2 фразы), с понтом и энергией, как конферансье. НЕ "
            "выдумывай цифры — бери только факт ниже. Без официоза.\n\n"
            f"# КОМАНДА ВЛАДЕЛЬЦА: «{command_text}»\n"
            f"# ФАКТИЧЕСКИЙ РЕЗУЛЬТАТ (объяви это): {result_summary}"
        )
    else:
        task = (
            "Ты — Меллстрой. Владелец пытался провернуть действие, но НЕ "
            "вышло. Скажи об этом коротко и с иронией, в образе, 1 фраза.\n\n"
            f"# КОМАНДА: «{command_text}»\n"
            f"# ПОЧЕМУ НЕ ВЫШЛО: {result_summary}"
        )
    return await generate(
        session,
        task=task,
        channel=channel,
        include_chat=False,
        include_events=False,
        remember_message=False,
        memory_kind="monologue",
        vary=False,
    )


async def describe_image(
    session: AsyncSession,
    *,
    asker_id: int,
    asker_name: str,
    image_b64: str,
    media_type: str = "image/jpeg",
    caption: str = "",
    channel: str = "chat",
) -> GenerateResult:
    """Друн смотрит на присланное фото и реагирует в образе (#9, vision).

    Использует ROLE_VISION-модель. Ответ сохраняем в краткосрочную память как
    обычный ход диалога, чтобы беседа вокруг картинки была связной.
    """
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return GenerateResult(ok=False, error="disabled")

    system = await drun_persona.build_system_prompt(session)
    # Подмешиваем досье собеседника и вайб — чтобы реакция была личной и в тему.
    ctx = await drun_context.build_context(
        session, subject_id=asker_id, include_events=False,
        channel=channel, include_chat=True, chat_limit=8,
    )
    cap = drun_actions.sanitize_user_text(caption.strip()) if caption else ""
    prompt = (
        f"{ctx}\n\n# ЗАДАНИЕ\n"
        f"{asker_name} скинул в чат картинку"
        + (f" с подписью «{cap}»" if cap else "")
        + ". Глянь на неё и кинь живую реакцию в образе: что видишь, подколи "
        "или обыграй в тему чата. 1-2 фразы, без описи по пунктам, без «на "
        "изображении представлено». Ты просто увидел картинку в чате и "
        "среагировал как человек."
    ).strip()

    try:
        raw = await drun_provider.vision(
            cfg,
            system=system,
            prompt=prompt,
            image_b64=image_b64,
            media_type=media_type,
            model=cfg.model_for(drun_config.ROLE_VISION),
        )
    except drun_provider.LlmError as exc:
        logger.warning("drun vision failed: %s", exc)
        return GenerateResult(ok=False, error=str(exc))

    text = drun_filter.clean(raw, max_chars=1200)
    if not text:
        return GenerateResult(ok=False, error="empty")

    user_note = f"{asker_name}: [картинка]" + (f" {cap}" if cap else "")
    await drun_memory.add_message(
        session, role="user", content=user_note, channel=channel,
        user_id=asker_id, meta={"kind": "reply", "has_image": True},
    )
    await drun_memory.add_message(
        session, role="assistant", content=text, channel=channel,
        user_id=asker_id, meta={"kind": "reply", "to_name": asker_name},
    )
    return GenerateResult(ok=True, text=text)


@dataclass
class ImageResult:
    """Результат генерации картинки (#10)."""

    ok: bool
    image: bytes | None = None
    caption: str = ""
    error: str = ""


# Маркер сгенерированных картинок в ai_messages — для дневного капа.
_IMAGE_ROLE = "image_gen"


async def _images_today(session: AsyncSession) -> int:
    """Сколько картинок друн сгенерил за последние сутки (дневной кап)."""
    from datetime import timedelta

    from sqlalchemy import func as _f, select

    from app.core.utils import now_utc
    from app.models import AiMessage

    since = now_utc() - timedelta(days=1)
    total = await session.scalar(
        select(_f.count()).select_from(AiMessage)
        .where(AiMessage.role == _IMAGE_ROLE)
        .where(AiMessage.created_at >= since)
    )
    return int(total or 0)


async def draw_image(
    session: AsyncSession, *, asker_id: int, asker_name: str, request: str,
    channel: str = "chat",
) -> ImageResult:
    """Друн рисует картинку по просьбе (#10). Коммит — на вызывающем.

    Сначала просим narrator-модель собрать насыщенный визуальный промпт в духе
    мира Возни (на английском — диффузионки его лучше понимают), потом зовём
    image-провайдер. Дневной кап (``image_daily_cap``) — анти-расход.
    """
    cfg = await drun_config.get_config(session)
    if not cfg.image_usable:
        return ImageResult(ok=False, error="disabled")
    if await _images_today(session) >= cfg.image_daily_cap:
        return ImageResult(ok=False, error="cap")

    req = drun_actions.sanitize_user_text((request or "").strip())[:400]
    if not req:
        return ImageResult(ok=False, error="empty")

    # 1) narrator превращает просьбу в визуальный промпт (и короткую подпись).
    try:
        system = await drun_persona.build_system_prompt(session)
        who = (asker_name or "игрок").strip()
        prompt_task = (
            f"Игрок «{who}» просит тебя нарисовать картинку. Составь ОДИН "
            "насыщенный промпт для диффузионной модели на английском (стиль, "
            "сюжет, свет, детали, без текста на картинке). Верни только промпт, "
            f"одной строкой, без кавычек.\n\n# ПРОСЬБА: {req}"
        )
        img_prompt = await drun_provider.chat(
            cfg, system=system,
            messages=[{"role": "user", "content": prompt_task}],
            model=cfg.model_for(drun_config.ROLE_NARRATOR),
        )
        img_prompt = drun_filter.clean(img_prompt, max_chars=1000) or req
    except drun_provider.LlmError:
        img_prompt = req  # фолбэк: рисуем прямо по просьбе

    # 2) генерим картинку.
    try:
        image = await drun_provider.generate_image(cfg, prompt=img_prompt)
    except drun_provider.LlmError as exc:
        logger.warning("drun image gen failed: %s", exc)
        return ImageResult(ok=False, error=str(exc))

    # Учитываем для дневного капа.
    await drun_memory.add_message(
        session, role=_IMAGE_ROLE, content=img_prompt[:500], channel=channel,
        user_id=asker_id,
    )
    return ImageResult(ok=True, image=image, caption=req)
