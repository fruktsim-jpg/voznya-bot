"""Мягкий антифлуд: защита от слишком частых команд одного пользователя.

Это НЕ игровые кулдауны (те хранятся в БД), а защита от случайного
дабл-клика и спама командами. Хранится в памяти процесса — этого достаточно,
так как речь о секундах.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from app.settings import balance


_COMMAND_ALIASES = {
    # economy/gameplay
    "ферма", "farm", "фарм", "казино", "casino", "ставка", "бой", "duel",
    "дуэль", "дуэлька", "го", "accept", "go", "клад", "claim", "снять",
    # info windows
    "баланс", "balance", "бал", "деньги", "кошелёк", "кошелек", "бабки",
    "профиль", "profile", "проф", "кто", "инвентарь", "инв", "рюкзак",
    "inventory", "inv", "ачивки", "achievements", "ачивы", "достижения",
    "помощь", "help", "старт", "start", "команды", "меню",
    # leaderboards
    "топ", "top", "рейтинг", "лидеры", "богачи", "богатые", "топнеделя",
    "weekly", "семьи", "families", "браки", "свадьбы", "ммр", "mmr",
    "топммр", "topmmr", "реп", "репутация", "rep", "reputation",
    "топреп", "toprep", "сезон", "season", "миссии", "missions",
    "топсезон", "topseason",
    # social/noisy
    "пидор", "pidor", "пара", "couple", "para", "осеменить", "изнасиловать",
    "выебать", "наплюхать", "сделать_беременной",
}


def _looks_like_command(text: str) -> bool:
    first = (text or "").strip().split(maxsplit=1)[0].lower()
    if not first:
        return False
    if first.startswith("/"):
        return True
    if "@" in first:
        first = first.split("@", 1)[0]
    return first in _COMMAND_ALIASES


class AntiFloodMiddleware(BaseMiddleware):
    """Отсекает команды, поданные чаще, чем раз в ANTIFLOOD_SECONDS."""

    def __init__(self) -> None:
        self._last_command: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            text = event.text or event.caption or ""
            if _looks_like_command(text):
                user_id = event.from_user.id
                now = time.monotonic()
                last = self._last_command.get(user_id, 0.0)
                if now - last < balance.ANTIFLOOD_SECONDS:
                    # Слишком часто — тихо игнорируем команду.
                    return None
                self._last_command[user_id] = now
        return await handler(event, data)
