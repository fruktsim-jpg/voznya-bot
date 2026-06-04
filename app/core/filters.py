"""Фильтры aiogram для команд на русском языке.

Telegram не всегда размечает кириллические команды (например, «/ферма»)
как сущности типа ``bot_command``. Поэтому стандартный фильтр Command
ненадёжен для русских команд. Здесь — собственный фильтр, который разбирает
текст сообщения вручную и работает с любыми командами.
"""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message


class RuCommand(BaseFilter):
    """Фильтр команды, заданной словом.

    Поддерживает команды как со слэшем (``/ферма``), так и без него (``ферма``),
    несколько алиасов (русских и английских), опциональный ``@botusername``
    и передаёт остаток строки в хендлер как ``command_args``.

    Пример::

        @router.message(RuCommand("ферма", "farm"))
        async def farm(message: Message, command_args: str): ...
    """

    def __init__(self, *commands: str, allow_no_prefix: bool = True) -> None:
        if not commands:
            raise ValueError("RuCommand требует хотя бы одну команду")
        self.commands = {c.lower() for c in commands}
        self.allow_no_prefix = allow_no_prefix

    async def __call__(self, message: Message) -> bool | dict:
        text = message.text or message.caption
        if not text:
            return False
        text = text.strip()

        first_token = text.split(maxsplit=1)[0]
        if first_token.startswith("/"):
            command = first_token[1:]
        elif self.allow_no_prefix:
            command = first_token
        else:
            return False

        if "@" in command:
            command = command.split("@", 1)[0]
        if command.lower() not in self.commands:
            return False

        parts = text.split(maxsplit=1)
        args = parts[1].strip() if len(parts) > 1 else ""
        return {"command_args": args}
