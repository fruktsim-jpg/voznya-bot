"""Реактивный друн: отвечает на обращения в чате и иногда встревает сам.

Триггеры ответа (любой достаточен):
* reply на сообщение бота;
* @упоминание бота (по ``bot_username``);
* имя-обращение в тексте («друн»/«drun», настраивается в ``name_triggers``);
* редкое случайное встревание (шанс ``random_butt_in_chance``).

Антиспам:
* глобальный кулдаун канала (``reply_cooldown_sec``) через таблицу cooldowns;
* дневной кап ответов (``posts_per_day_max``) через счётчик в ai_messages;
* при адресном обращении кулдаун мягче (всё равно отвечаем людям), но кап
  соблюдаем всегда.

Хендлер ставится ПОСЛЕ командных роутеров — чтобы не перехватывать команды.
"""

from __future__ import annotations

import random
import re
from datetime import timedelta

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logger import get_logger
from app.core.utils import now_utc
from app.features.drun import config as drun_config
from app.features.drun import agent as drun_agent
from app.features.drun import memory as drun_memory
from app.features.drun import service as drun_service
from app.models import Cooldown

logger = get_logger(__name__)

router = Router(name="drun_reply")

_COOLDOWN_ACTION = "drun_reply"
_COOLDOWN_USER = 0  # общий (канальный) кулдаун, не привязан к игроку


def _display_name(message: Message) -> str:
    u = message.from_user
    if u is None:
        return "кто-то"
    return u.full_name or (f"@{u.username}" if u.username else f"игрок#{u.id}")


def _is_reply_to_bot(message: Message, bot_id: int) -> bool:
    r = message.reply_to_message
    return bool(r and r.from_user and r.from_user.id == bot_id)


def _has_mention(message: Message, bot_username: str) -> bool:
    if not bot_username:
        return False
    text = (message.text or message.caption or "").lower()
    return f"@{bot_username.lower()}" in text


def _has_name_trigger(message: Message, triggers: list[str]) -> bool:
    text = (message.text or message.caption or "").lower()
    # Слово целиком, чтобы «друн» ловился, а «друнгель» — нет.
    return any(re.search(rf"\b{re.escape(t)}\b", text) for t in triggers)


async def _cooldown_active(session: AsyncSession) -> bool:
    cd = await session.get(Cooldown, (_COOLDOWN_USER, _COOLDOWN_ACTION))
    return cd is not None and cd.available_at > now_utc()


async def _set_cooldown(session: AsyncSession, seconds: int) -> None:
    available = now_utc() + timedelta(seconds=max(1, seconds))
    cd = await session.get(Cooldown, (_COOLDOWN_USER, _COOLDOWN_ACTION))
    if cd is None:
        session.add(
            Cooldown(
                user_id=_COOLDOWN_USER,
                action=_COOLDOWN_ACTION,
                available_at=available,
            )
        )
    else:
        cd.available_at = available


@router.message(F.text | F.caption)
async def on_chat_message(message: Message, session: AsyncSession) -> None:
    """Решает, отвечать ли друну на это сообщение, и отвечает в образе."""
    settings = get_settings()
    if message.chat.id != settings.chat_id:
        return
    user = message.from_user
    if user is None or user.is_bot:
        return

    cfg = await drun_config.get_config(session)
    if not cfg.usable or not cfg.reply_enabled:
        return

    bot_id = message.bot.id if message.bot else 0
    addressed = (
        _is_reply_to_bot(message, bot_id)
        or _has_mention(message, settings.bot_username)
        or _has_name_trigger(message, cfg.name_triggers)
    )

    # Адресные сообщения отвечаем всегда (в рамках капа); иначе — редкий рандом,
    # и то лишь когда в чате есть «движ» (несколько свежих реплик подряд), а не
    # на одинокое сообщение в тишине — так вкиды реже и всегда в тему. Дешёвую
    # проверку рандома делаем ПЕРВОЙ, чтобы не бить в БД на каждое сообщение.
    if not addressed:
        if random.random() >= max(0.0, cfg.random_butt_in_chance):
            return
        chat_hot = await drun_memory.recent_chat_count(session, channel="chat", seconds=180)
        if chat_hot < 4:
            return

    # Дневной кап — предел расходов/спама на АВТОНОМНЫЕ вкиды. Адресные
    # обращения (reply/упоминание/имя) кап НЕ глушит: если человек прямо
    # спрашивает друна, он обязан ответить, иначе бот выглядит сломанным.
    if not addressed:
        replies_today = await drun_memory.count_replies_today(session, channel="chat")
        if replies_today >= cfg.posts_per_day_max:
            return

    # Кулдаун канала: для адресных — мягче (отвечаем людям, но не строчим).
    if await _cooldown_active(session):
        if not addressed:
            return
        # адресное во время кулдауна пропускаем, только если кулдаун ещё «горячий»
        # (защита от строчки из @упоминаний) — но обычно отвечаем.

    text = (message.text or message.caption or "").strip()
    if addressed:
        # Owner-команда? Если автор — owner и реплика похожа на действие
        # («дай всем…», «сбрось кд…», «разыграй…»), пробуем агентный путь:
        # распознаём намерение и реально выполняем над миром. Обычная болтовня
        # owner'а сюда не попадает (pre-filter + планировщик вернёт none).
        from app.config import get_settings as _gs

        if _gs().is_admin(user.id) and drun_agent.looks_like_action(text):
            try:
                outcome = await drun_agent.try_handle(
                    session, owner_id=user.id, text=text
                )
            except Exception:  # noqa: BLE001
                logger.warning("drun agent failed", exc_info=True)
                outcome = None
            if outcome is not None and outcome.handled:
                logger.info(
                    "drun owner-command by %s: tool=%s ok=%s",
                    user.id, outcome.tool, outcome.ok,
                )
                # Спавн клада исполняется отдельно (нужен bot + своя сессия).
                if outcome.ok and outcome.summary == "__spawn_treasure__":
                    try:
                        from app.core.db import get_sessionmaker
                        from app.features.treasure.service import spawn_treasure

                        await session.commit()
                        await spawn_treasure(
                            message.bot, get_sessionmaker(), settings.chat_id
                        )
                        await _set_cooldown(session, cfg.reply_cooldown_sec)
                    except Exception:  # noqa: BLE001
                        logger.warning("owner spawn_treasure failed", exc_info=True)
                        await message.reply("клад застрял в кармане, попробуй ещё", parse_mode=None)
                    return
                # Фиксируем результат инструмента ДО медленного announce-вызова к
                # LLM: иначе строковые блокировки FOR UPDATE на затронутых
                # игроках висят весь сетевой запрос и тормозят их операции.
                await session.commit()
                await _set_cooldown(session, cfg.reply_cooldown_sec)
                announce = await drun_service.announce_action(
                    session,
                    owner_name=_display_name(message),
                    command_text=text,
                    result_summary=outcome.summary,
                    ok=outcome.ok,
                )
                out_text = (
                    announce.text if announce.ok and announce.text
                    else (f"Сделано: {outcome.summary}" if outcome.ok
                          else f"Не вышло: {outcome.summary}")
                )
                await message.reply(out_text, parse_mode=None)
                return

        # Прямое обращение — отвечаем НА КОНКРЕТНУЮ реплику человека.
        result = await drun_service.respond(
            session,
            asker_id=user.id,
            asker_name=_display_name(message),
            text=text,
        )
    else:
        # Случайный вкид — это НЕ ответ на вопрос, а живое встревание по
        # настроению чата. Берём observe-режим (читает живой чат и вкидывает
        # реплику в тему), иначе друн отвечал бы на случайную фразу как на
        # адресованный ему вопрос и выдавал бы несвязную дичь.
        result = await drun_service.observe(session, subject_id=user.id)
    if not result.ok:
        return

    await _set_cooldown(session, cfg.reply_cooldown_sec)
    # Текст друна — свободный (может содержать < > & и т.п.). Шлём как обычный
    # текст без разметки, иначе Telegram падает на HTML-парсинге.
    out = result.text
    econ = getattr(result, "econ", None)
    if econ is not None and getattr(econ, "ok", False):
        # Маленькая прозрачная пометка о реальном движении ешек.
        if econ.kind == "tax":
            out += f"\n\n💸 Налоговая друна: −{econ.applied} ешек (баланс: {econ.balance})"
        else:
            out += f"\n\n🎁 Друн сжалился: +{econ.applied} ешек (баланс: {econ.balance})"
    # Адресные — реплаем (видно, кому отвечает); вкиды — обычным сообщением.
    if addressed:
        await message.reply(out, parse_mode=None)
    else:
        await message.answer(out, parse_mode=None)
