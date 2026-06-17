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
    "Тебе написал человек (реплика — в конце). Ответь как живой Меллстрой и "
    "СООТВЕТСТВУЙ МОМЕНТУ: на короткое — коротко 1-2 фразы, не вываливай "
    "простыню и не перечисляй людей. Прочитай ЖИВОЙ ЧАТ, пойми тему и "
    "настроение, отвечай как часть беседы. Учитывай ТВОЁ ОТНОШЕНИЕ к нему. "
    "Зови по нику, жёстко и дерзко, на суть. Не ассистент."
)


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
    channel: str = "chat",
    include_events: bool = True,
    include_chat: bool = True,
    trigger_event_id: int | None = None,
    remember_message: bool = True,
    memory_user_content: str | None = None,
    memory_kind: str = "monologue",
    allow_actions: bool = False,
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
    )

    history = await drun_memory.recent_messages(session, channel=channel, limit=8)
    messages: list[dict[str, str]] = [
        {"role": m.role, "content": m.content}
        for m in history
        if m.role in {"user", "assistant"}
    ]
    user_content = (f"{ctx}\n\n# ЗАДАНИЕ\n{task}" if ctx else task).strip()
    messages.append({"role": "user", "content": user_content})

    try:
        raw = await drun_provider.chat(cfg, system=system, messages=messages)
    except drun_provider.LlmError as exc:
        logger.warning("drun generate failed: %s", exc)
        return GenerateResult(ok=False, error=str(exc))

    text = drun_filter.clean(raw, max_chars=1200)
    if not text:
        return GenerateResult(ok=False, error="empty")

    # Экономическая выходка (налог/подачка), если друн вставил директиву и
    # власть включена. Применяем к собеседнику; директиву вырезаем из текста.
    econ_result = None
    if allow_actions and cfg.econ_enabled:
        econ_result = await drun_actions.apply_if_any(
            session, cfg=cfg, target_id=subject_id, text=text
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
            meta={"kind": memory_kind},
        )

    return GenerateResult(ok=True, text=text, econ=econ_result)


async def observe(
    session: AsyncSession,
    *,
    subject_id: int | None = None,
    channel: str = "chat",
) -> GenerateResult:
    """Одиночное наблюдение про мир/игрока (ручной триггер, MVP)."""
    task = await drun_config.get_prompt(
        session, drun_config.PROMPT_OBSERVATION, _DEFAULT_OBSERVATION
    )
    return await generate(
        session, task=task, subject_id=subject_id, channel=channel
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
) -> GenerateResult:
    """Ответ друна на обращение игрока в чате (по контексту беседы)."""
    template = await drun_config.get_prompt(
        session, drun_config.PROMPT_REPLY, _DEFAULT_REPLY
    )
    task = f'{template}\n\nРеплика игрока {asker_name}: «{text.strip()}»'
    return await generate(
        session,
        task=task,
        subject_id=asker_id,
        channel=channel,
        memory_user_content=f"{asker_name}: {text.strip()}",
        memory_kind="reply",
        allow_actions=True,
    )
