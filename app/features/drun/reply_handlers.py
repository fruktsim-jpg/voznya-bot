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
from app.features.drun import deferral as drun_deferral
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


async def _build_mentions_line(session: AsyncSession, targets: list[dict]) -> str:
    """HTML-строка с кликабельными тегами игроков и их новым балансом.

    ``targets`` — из ToolResult.meta: [{id, delta, balance}, ...]. Тегаем через
    ``tg://user?id=`` (кликабельно и пингует, если игрок в чате). Имена и всё
    динамическое экранируем — строку шлём в parse_mode=HTML.
    """
    from html import escape

    from app.core.money import money
    from app.features.drun.names import name_for, resolve_names

    items = [t for t in targets if isinstance(t, dict) and t.get("id")]
    if not items:
        return ""
    names = await resolve_names(session, [t["id"] for t in items])
    parts: list[str] = []
    for t in items:
        uid = t["id"]
        nm = escape(name_for(names, uid))
        mention = f'<a href="tg://user?id={uid}">{nm}</a>'
        delta = int(t.get("delta", 0) or 0)
        bal = t.get("balance")
        sign = "+" if delta > 0 else ""
        tail = f" ({sign}{money(delta)}"
        if bal is not None:
            tail += f", стало {money(int(bal))}"
        tail += ")"
        parts.append(mention + escape(tail))
    return " · ".join(parts)


def _is_reply_to_bot(message: Message, bot_id: int) -> bool:
    r = message.reply_to_message
    return bool(r and r.from_user and r.from_user.id == bot_id)


def _reply_excerpt(message: Message) -> str:
    """Текст сообщения, на которое отвечают реплаем (нить беседы для друна)."""
    r = message.reply_to_message
    if r is None:
        return ""
    return (r.text or r.caption or "").strip()


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


async def _do_draw(
    session: AsyncSession, message: Message, *, request: str, cfg,
) -> None:
    """Генерит картинку по просьбе и кидает её фото в чат (#10).

    Вынесено сюда, т.к. нужен bot-объект для отправки фото. Картинку рисует
    service.draw_image (с дневным капом и предохранителями).
    """
    from aiogram.types import BufferedInputFile

    try:
        res = await drun_service.draw_image(
            session,
            asker_id=message.from_user.id if message.from_user else 0,
            asker_name=_display_name(message),
            request=request,
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("drun draw failed", exc_info=True)
        await message.reply("рука дрогнула, не нарисовалось", parse_mode=None)
        return
    if not res.ok or not res.image:
        note = {
            "disabled": "я сейчас не рисую",
            "cap": "на сегодня дорисовался, приходи завтра",
            "empty": "а что рисовать-то?",
        }.get(res.error, "не нарисовалось, бывает")
        await message.reply(note, parse_mode=None)
        return
    await _set_cooldown(session, cfg.reply_cooldown_sec)
    photo = BufferedInputFile(res.image, filename="drun.png")
    await message.reply_photo(photo, caption=(res.caption or None))


@router.message((F.text | F.caption) & ~F.photo)
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

    # АГЕНТНОСТЬ вместо слепой монетки: для НЕадресных реплик друн «решает», а
    # не бросает кубик. Слой восприятия (perceive) по тексту выбирает намерение
    # (подколоть/поддержать/подлить движа/смолчать) и силу позыва. Молчание —
    # это тоже решение, и обычно правильное.
    engagement = None
    if not addressed:
        from app.features.drun import perceive as drun_perceive

        text = (message.text or message.caption or "").strip()
        addressed_other = message.reply_to_message is not None
        # Лексическое решение БЕЗ обращения к БД (chat_hot=0): сигнальные ветки
        # (наезд/хвастовство/скука/вопрос/его тема) не зависят от накала чата.
        engagement = drun_perceive.decide_engagement(
            text,
            chat_hot=0,
            mentions_drun_topic=drun_perceive.mentions_drun_topic(text),
            addressed_other=addressed_other,
        )
        if not engagement.wants_in:
            # Нет явного сигнала — остаётся лишь «тонкий вкид в оживлённый чат».
            # Этот путь даёт слабый позыв (urge≈0.15), поэтому СНАЧАЛА дешёвый
            # бросок монетки (как раньше — чтобы не бить COUNT-ом в БД на каждое
            # сообщение), и только если он прошёл — подтверждаем накал чата.
            if random.random() >= max(0.0, cfg.random_butt_in_chance):
                return
            chat_hot = await drun_memory.recent_chat_count(
                session, channel="chat", seconds=180
            )
            if chat_hot < 6:
                return
            engagement = drun_perceive.decide_engagement(
                text, chat_hot=chat_hot,
                mentions_drun_topic=drun_perceive.mentions_drun_topic(text),
                addressed_other=addressed_other,
            )
            if not engagement.wants_in:
                return
        else:
            # Явный сигнал есть. Сила позыва × частота = вероятность вставить
            # слово: сильные сигналы (наезд/скука) проходят почти всегда, слабые
            # реже; общий темп регулируется одной ручкой random_butt_in_chance.
            gate = engagement.urge * (1.0 + max(0.0, cfg.random_butt_in_chance) * 4.0)
            if random.random() >= min(0.95, gate):
                # ЗАМЕТИЛ, НО СМОЛЧАЛ. Живой человек не реагирует на всё сразу —
                # но и не забывает. Сигнальные реплики (наезд/хвастовство/
                # обещание) друн иногда кладёт в «отложку», чтобы припомнить их
                # ПОЗЖЕ, без повода (см. autonomous._notice_pattern → deferral).
                # Это ломает паттерн «стимул→мгновенный ответ».
                if engagement.intent.value in ("roast", "hype") and random.random() < 0.5:
                    try:
                        await drun_deferral.stash(
                            session,
                            user_id=user.id,
                            name=_display_name(message),
                            gist=text[:160],
                            kind=engagement.intent.value,
                        )
                        await session.commit()
                    except Exception:  # noqa: BLE001
                        logger.debug("deferral stash on silence failed", exc_info=True)
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
                # Рисование (#10): генерим картинку и кидаем фото в чат.
                if outcome.ok and outcome.summary == "__draw_image__":
                    await _do_draw(
                        session, message,
                        request=(outcome.meta or {}).get("request", "") or text,
                        cfg=cfg,
                    )
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
                # Кликабельные теги затронутых игроков + их новый баланс
                # (победители розыгрыша, кому выдал/снял). Отдельным сообщением
                # в HTML, чтобы свободный текст друна не ломал парсер.
                if outcome.ok:
                    try:
                        line = await _build_mentions_line(
                            session, outcome.meta.get("targets") or []
                        )
                        if line:
                            await message.answer(line, parse_mode="HTML")
                    except Exception:  # noqa: BLE001
                        logger.debug("mentions line failed", exc_info=True)
                return

        # Прямое обращение — отвечаем НА КОНКРЕТНУЮ реплику человека. Если это
        # реплай на сообщение друна — даём ему текст той реплики как нить.
        reply_ctx = _reply_excerpt(message) if _is_reply_to_bot(message, bot_id) else None
        # Восприятие и для АДРЕСНОГО обращения: ROAST/HYPE-сигналы (хвастовство,
        # джекпот, наезд) — это повод друну подумать про эконом-выходку
        # (налог/подачку), а не только генерить тон. Сама директива остаётся
        # ОПЦИОНАЛЬНОЙ — модель решает, вставлять её или нет; econ.apply имеет
        # все предохранители (cap/cooldown/clamp), поэтому подсказка безопасна.
        from app.features.drun import perceive as drun_perceive

        addr_engagement = drun_perceive.decide_engagement(
            text, chat_hot=0,
            mentions_drun_topic=drun_perceive.mentions_drun_topic(text),
            addressed_other=False,
        )
        result = await drun_service.respond(
            session,
            asker_id=user.id,
            asker_name=_display_name(message),
            text=text,
            reply_context=reply_ctx,
            intent_note=addr_engagement.reason if addr_engagement.wants_in else None,
            intent_kind=addr_engagement.intent.value if addr_engagement.wants_in else None,
            urge=addr_engagement.urge if addr_engagement.wants_in else 0.5,
        )
    else:
        # Спонтанное встревание по решению восприятия. Передаём НАМЕРЕНИЕ
        # (зачем влезаем) — генерация целенаправленна, а не «ляпни случайное».
        result = await drun_service.observe(
            session,
            subject_id=user.id,
            intent_note=engagement.reason if engagement else None,
            intent_kind=engagement.intent.value if engagement else None,
            urge=engagement.urge if engagement else 0.0,
        )
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


@router.message(F.photo)
async def on_photo_message(message: Message, session: AsyncSession) -> None:
    """Друн анализирует присланное фото, но ТОЛЬКО когда к нему обратились (#9).

    Чтобы не жечь vision-модель на каждую картинку в чате, реагируем лишь если
    фото — это reply на бота, @упоминание или имя-обращение в подписи.
    """
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
    if not addressed:
        return
    if await _cooldown_active(session):
        return

    # Берём самый крупный вариант фото (последний в списке размеров).
    photo = message.photo[-1] if message.photo else None
    if photo is None or message.bot is None:
        return
    try:
        import base64
        from io import BytesIO

        buf = BytesIO()
        await message.bot.download(photo, destination=buf)
        image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        logger.warning("drun photo download failed", exc_info=True)
        return

    result = await drun_service.describe_image(
        session,
        asker_id=user.id,
        asker_name=_display_name(message),
        image_b64=image_b64,
        media_type="image/jpeg",
        caption=message.caption or "",
    )
    if not result.ok:
        return
    await _set_cooldown(session, cfg.reply_cooldown_sec)
    await message.reply(result.text, parse_mode=None)


_MEDIA_KIND_RU = {
    "sticker": "стикер", "voice": "голосовуху", "video_note": "кружок",
    "video": "видео", "animation": "гифку", "audio": "аудио",
    "document": "файл", "poll": "опрос",
}


def _nonphoto_media_kind(message: Message) -> str | None:
    """Тип не-фото вложения (фото обрабатывает отдельный vision-хендлер)."""
    if message.sticker:
        return "sticker"
    if message.voice:
        return "voice"
    if message.video_note:
        return "video_note"
    if message.animation:
        return "animation"
    if message.video:
        return "video"
    if message.audio:
        return "audio"
    if message.document:
        return "document"
    if message.poll:
        return "poll"
    return None


@router.message(F.sticker | F.voice | F.video_note | F.video | F.animation | F.audio | F.document)
async def on_media_message(message: Message, session: AsyncSession) -> None:
    """Друн реагирует, когда к нему обращаются НЕ-фото медиа (реплай/упоминание).

    Раньше друн был глух к стикерам/голосовухам/кружкам в свой адрес — они не
    попадали ни в текстовый, ни в фото-хендлер. Это часть агентности: ответ
    другу стикером — это социальный жест, и друн должен на него реагировать,
    а не молчать. Vision сюда не зовём (контента модели не даём), реагируем по
    самому ФАКТУ и подписи.
    """
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
    # На не-фото медиа реагируем ТОЛЬКО при явном обращении — иначе друн бы
    # комментировал каждый стикер в чате (спам). Ambient-восприятие таких медиа
    # уже идёт через ears → живой чат, этого достаточно для фона.
    if not addressed:
        return
    if await _cooldown_active(session):
        return

    kind = _nonphoto_media_kind(message)
    kind_ru = _MEDIA_KIND_RU.get(kind or "", "что-то")
    caption = (message.caption or "").strip()
    reply_ctx = _reply_excerpt(message) if _is_reply_to_bot(message, bot_id) else None
    parts = [f"{_display_name(message)} кинул(а) тебе {kind_ru}"]
    if caption:
        parts.append(f"с подписью «{caption}»")
    note = " ".join(parts) + ". Среагируй живо и коротко в образе (1 фраза)."
    result = await drun_service.respond(
        session,
        asker_id=user.id,
        asker_name=_display_name(message),
        text=note,
        reply_context=reply_ctx,
    )
    if not result.ok:
        return
    await _set_cooldown(session, cfg.reply_cooldown_sec)
    await message.reply(result.text, parse_mode=None)
