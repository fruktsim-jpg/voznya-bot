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

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.features.drun import config as drun_config
from app.features.drun import context as drun_context
from app.features.drun import actions as drun_actions
from app.features.drun import filter as drun_filter
from app.features.drun import memory as drun_memory
from app.features.drun import persona as drun_persona
from app.features.drun import provider as drun_provider

logger = get_logger(__name__)

_DEFAULT_OBSERVATION = (
    "Вкинь в чат короткую живую реплику — глянь о чём базарят и вцепись в тему "
    "или вкинь мем. 1-2 фразы, без статистики и пересказа событий."
)
_DEFAULT_REACTION = (
    "Среагируй одной живой репликой на то, что в чате/мире — коротко, в тему, "
    "по настроению. Без лент и списков."
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
    "под него и его состояние (см. правила режимов). Не ассистент, но и не "
    "мимо темы."
)


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
    """
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return GenerateResult(ok=False, error="disabled")

    system = await drun_persona.build_system_prompt(
        session, econ_enabled=(allow_actions and cfg.econ_enabled)
    )
    ctx = await drun_context.build_context(
        session,
        subject_id=subject_id,
        include_events=include_events,
        channel=channel,
        include_chat=include_chat,
        chat_limit=chat_limit,
        query=query,
    )

    history = await drun_memory.recent_messages(session, channel=channel, limit=10)
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
    user_content = (f"{ctx}\n\n# ЗАДАНИЕ\n{task}" if ctx else task).strip()
    messages.append({"role": "user", "content": user_content})

    try:
        raw = await drun_provider.chat(
            cfg, system=system, messages=messages,
            model=cfg.model_for(role or drun_config.ROLE_NARRATOR),
        )
    except drun_provider.LlmError as exc:
        logger.warning("drun generate failed: %s", exc)
        return GenerateResult(ok=False, error=str(exc))

    text = drun_filter.clean(raw, max_chars=1200)
    if not text:
        return GenerateResult(ok=False, error="empty")

    # Экономическая выходка (налог/подачка), если друн вставил директиву и
    # власть включена. Применяем к субъекту реплики.
    #
    # ``asker_id`` отделён от ``subject_id`` намеренно: для адресного ответа
    # (respond) автор обращения = субъект, и self-grant-блок ловит абуз через
    # эхо директивы. Для спонтанного вкида (observe) человек НЕ обращался к
    # друну — это инициатива друна, и блокировать grant как «самоначисление»
    # неправильно (игрок не мог ничего «попросить»). По умолчанию asker_id=
    # subject_id, чтобы старое поведение respond не сломалось; observe явно
    # передаёт asker_id=None.
    econ_result = None
    if allow_actions and cfg.econ_enabled:
        effective_asker = asker_id if asker_id is not None else subject_id
        econ_result = await drun_actions.apply_if_any(
            session, cfg=cfg, target_id=subject_id, text=text,
            asker_id=effective_asker, intent_kind=intent_kind,
        )
    if drun_actions.parse(text) is not None:
        text = drun_actions.strip_directives(text)
        if not text:
            text = "..."

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
            meta={"kind": memory_kind, "to_name": subject_name},
        )

    return GenerateResult(ok=True, text=text, econ=econ_result)


async def observe(
    session: AsyncSession,
    *,
    subject_id: int | None = None,
    channel: str = "chat",
    intent_note: str | None = None,
    intent_kind: str | None = None,
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
    if intent_kind and cfg.econ_enabled and subject_id is not None:
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
    try:
        from app.features.drun import affinity as drun_affinity

        await drun_affinity.record_interaction(session, asker_id, safe_text)
    except Exception:  # noqa: BLE001
        logger.debug("respond affinity update failed", exc_info=True)
    # Фактический вопрос (погода/новости/курс/«что такое») → подтягиваем свежие
    # данные из интернета, чтобы друн не выдумывал. Для внутренних тем Возни и
    # обычной болтовни веб не дёргается (см. websearch.looks_factual).
    web_block = ""
    try:
        from app.features.drun import websearch as drun_web

        web_block = await drun_web.auto_context(session, safe_text)
    except Exception:  # noqa: BLE001
        logger.debug("respond web auto_context failed", exc_info=True)
    # «Чувство комнаты»: если чат сейчас абузит бота (каждый второй на нём
    # висит) — подмешиваем установку отвечать короче/суше и переводить движ на
    # общение людей. В обычном режиме подсказка пустая и ничего не меняет.
    room_block = ""
    try:
        from app.features.drun import governor as drun_governor

        verdict = await drun_governor.assess(session, channel=channel)
        if verdict.throttle and verdict.note:
            room_block = f"# РЕЖИМ КОМНАТЫ: {verdict.note}"
    except Exception:  # noqa: BLE001
        logger.debug("respond governor assess failed", exc_info=True)
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
    if intent_kind and cfg.econ_enabled:
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
