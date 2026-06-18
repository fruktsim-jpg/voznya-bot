"""Middleware: «уши» друна — пишет реплики игроков в краткосрочную память.

Чтобы друн отвечал ПО КОНТЕКСТУ и помнил, о чём говорят люди, бот должен
слышать чат. Этот middleware на каждое текстовое сообщение из ЦЕЛЕВОГО чата
сохраняет реплику в ``ai_messages`` (role='chat', ник в meta). Только чтение
мира; запись идёт в ту же сессию и фиксируется DbSessionMiddleware.

Лимиты в пределах разумного:
* только целевой групповой чат (не личка, не другие группы);
* только непустой текст; команды и слишком короткий мусор пропускаем;
* длину реплики режет ``memory.capture_chat`` (анти-раздувание токенов).

Сбой записи НИКОГДА не должен ломать доставку сообщения игроку — всё в
try/except с тихим логом.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logger import get_logger
from app.features.drun import memory as drun_memory

logger = get_logger(__name__)

# Командные слова бота (без слэша их тоже распознаёт RuCommand). Реплики,
# начинающиеся с них, — это игровые команды, а не живой разговор. Их НЕ пишем
# в память друна, иначе «чат» превращается в спам «бой/казино/профиль/ферма».
_COMMAND_WORDS = frozenset(
    {
        "ачивки", "achievements", "ачивы", "достижения",
        "баланс", "balance", "бал", "деньги", "кошелёк", "кошелек", "бабки",
        "бан", "ban", "бой", "duel", "дуэль", "дуэлька",
        "бонус", "daily", "дейли", "брак", "marriage", "жена", "муж",
        "варн", "warn", "пред", "выдать", "give", "го", "accept", "go",
        "да", "yes", "друн", "drun", "жениться", "marry", "свадьба",
        "предложение", "забрать", "take", "инвентарь", "инв", "рюкзак",
        "inventory", "inv", "инфо", "info", "казино", "casino",
        "кейс", "case", "кейсы", "cases", "кик", "kick", "клад",
        "spawntreasure", "кто", "магазин", "shop", "подарки", "gifts",
        "миссии", "missions", "ммр", "mmr", "рейтинг", "моиподарки",
        "mygifts", "мут", "mute", "открыть", "open", "пара", "couple",
        "para", "пидор", "pidor", "помощь", "help", "старт", "start",
        "команды", "меню", "профиль", "profile", "проф", "разбан", "unban",
        "развод", "divorce", "развестись", "разрыв", "расстаться",
        "размут", "unmute", "реп", "репутация", "rep", "reputation",
        "сезон", "season", "семьи", "families", "браки", "свадьбы",
        "снять", "claim", "снятьварн", "unwarn", "стартсезон", "startseason",
        "топ", "top", "лидеры", "богачи", "богатые", "топммр", "topmmr",
        "топнеделя", "weekly", "топреп", "toprep", "топсезон", "topseason",
        "ферма", "farm", "фарм", "финалсезон", "finalizeseason",
        "осеменить", "изнасиловать", "выебать", "наплюхать",
        "modinfo", "модинфо", "topup",
    }
)

# Короткие реплики-«поддакивания», не несущие смысла для диалога.
_NOISE = frozenset({"+", "-", "го", "да", "нет", "ок", "ok", "топ", "лол", "ха"})


def _is_command_or_noise(text: str) -> bool:
    """True, если реплика — игровая команда, число или бессмысленный мусор."""
    low = text.lower().strip()
    first = low.split(maxsplit=1)[0].strip(".,!?:;")
    if first in _COMMAND_WORDS:
        return True
    if low in _NOISE:
        return True
    # Чисто число/ставка («1300», «на 1300», «бой 100» уже отсеян выше).
    compact = low.replace(" ", "")
    if compact.isdigit():
        return True
    # Слишком короткий огрызок (< 4 символов) — не диалог.
    if len(low) < 4:
        return True
    return False



def _display_name(message: Message) -> str:
    u = message.from_user
    if u is None:
        return "кто-то"
    if u.full_name:
        return u.full_name
    if u.username:
        return f"@{u.username}"
    return f"игрок#{u.id}"


def _detect_media(message: Message) -> str | None:
    """Тип вложения сообщения (для восприятия друном), либо None для чистого текста.

    Друн должен ЧУВСТВОВАТЬ форму активности: стикер/голосовуха/кружок/фото —
    это разный социальный сигнал, а не «пустое сообщение». Возвращаем короткую
    метку, которую видно и в памяти, и в контексте.
    """
    if message.photo:
        return "photo"
    if message.sticker:
        return "sticker"
    if message.animation:
        return "gif"
    if message.voice:
        return "voice"
    if message.video_note:
        return "video_note"
    if message.video:
        return "video"
    if message.audio:
        return "audio"
    if message.document:
        return "document"
    if message.poll:
        return "poll"
    if message.dice:
        return "dice"
    if message.contact:
        return "contact"
    if message.location:
        return "location"
    return None


def _reply_perception(message: Message, bot_id: int) -> dict[str, Any]:
    """Структура ответа: кому отвечает автор и на какой текст (нить беседы).

    Без этого друн видит плоский список реплик и не понимает, что условный
    «Вася» отвечает «Пете», а не в пустоту — или что это ответ на его же реплику.
    """
    r = message.reply_to_message
    if r is None:
        return {}
    out: dict[str, Any] = {}
    ru = r.from_user
    if ru is not None and bot_id and ru.id == bot_id:
        out["reply_to_bot"] = True
        out["reply_to_name"] = "тебе (друну)"
    elif ru is not None:
        out["reply_to_name"] = ru.full_name or (
            f"@{ru.username}" if ru.username else f"игрок#{ru.id}"
        )
    excerpt = (r.text or r.caption or "").strip()
    if excerpt:
        out["reply_excerpt"] = excerpt
    elif r.sticker or r.photo or r.voice or r.video_note or r.animation:
        out["reply_excerpt"] = "[медиа]"
    return out


class DrunEarsMiddleware(BaseMiddleware):
    """Сохраняет реплики игроков целевого чата в память друна."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            if isinstance(event, Message):
                await self._maybe_capture(event, data)
        except Exception:  # noqa: BLE001
            logger.debug("drun ears capture failed", exc_info=True)
        return await handler(event, data)

    async def _maybe_capture(self, message: Message, data: dict[str, Any]) -> None:
        settings = get_settings()
        # Слушаем только целевой групповой чат.
        if message.chat.id != settings.chat_id:
            return
        user = message.from_user
        if user is None or user.is_bot:
            return
        text = (message.text or message.caption or "").strip()
        media = _detect_media(message)
        # Чистый текст-команда («/...») не интересен. Но медиа-команд не бывает,
        # поэтому фильтр команд применяем только к тексту без вложения.
        if text.startswith("/") and media is None:
            return
        if text and media is None and _is_command_or_noise(text):
            # Игровые команды (бой/казино/...) и мусор не пишем в память друна.
            return
        if not text and media is None:
            return
        session: AsyncSession | None = data.get("session")
        if session is None:
            return
        bot_id = message.bot.id if message.bot else 0
        reply = _reply_perception(message, bot_id)
        await drun_memory.capture_chat(
            session,
            user_id=user.id,
            name=_display_name(message),
            content=text,
            media=media,
            reply_to_name=reply.get("reply_to_name"),
            reply_to_bot=bool(reply.get("reply_to_bot")),
            reply_excerpt=reply.get("reply_excerpt"),
        )
