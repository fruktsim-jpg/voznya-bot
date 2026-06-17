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
from app.features.drun import filter as drun_filter
from app.features.drun import memory as drun_memory
from app.features.drun import persona as drun_persona
from app.features.drun import provider as drun_provider

logger = get_logger(__name__)

_DEFAULT_OBSERVATION = (
    "Оглядись по сторонам и брось одно живое наблюдение про то, что сейчас "
    "творится в мире Возни. Опирайся на данные выше."
)
_DEFAULT_REACTION = (
    "Среагируй коротко и в образе на самое заметное из последних событий."
)
_DEFAULT_REPLY = (
    "К тебе обратился игрок (его реплика — в конце). Ответь коротко и в образе, "
    "ПО СУЩЕСТВУ его сообщения и с учётом того, о чём недавно говорили в чате. "
    "Зови людей по нику, а не по номеру. Не подыгрывай как ассистент."
)


@dataclass
class GenerateResult:
    """Результат генерации реплики друна."""

    ok: bool
    text: str = ""
    error: str = ""


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
) -> GenerateResult:
    """Генерирует одну реплику друна под конкретное задание ``task``."""
    cfg = await drun_config.get_config(session)
    if not cfg.usable:
        return GenerateResult(ok=False, error="disabled")

    system = await drun_persona.build_system_prompt(session)
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

    text = drun_filter.clean(raw, max_chars=600)
    if not text:
        return GenerateResult(ok=False, error="empty")

    if remember_message:
        # Сохраняем задание и ответ в краткосрочную память канала.
        await drun_memory.add_message(
            session,
            role="user",
            content=task,
            channel=channel,
            user_id=subject_id,
            trigger_event_id=trigger_event_id,
        )
        await drun_memory.add_message(
            session,
            role="assistant",
            content=text,
            channel=channel,
            user_id=subject_id,
            trigger_event_id=trigger_event_id,
        )

    return GenerateResult(ok=True, text=text)


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
    )
