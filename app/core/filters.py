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
    """Фильтр команды, заданной словом (без слэша).

    Поддерживает несколько алиасов, опциональный ``@botusername`` и
    передаёт остаток строки в хендлер как ``command_args``.

    Пример::

        @router.message(RuCommand("ферма"))
        async def farm(message: Message, command_args: str): ...
    """

    def __init__(self, *commands: str, prefix: str = "/") -> None:
        if not commands:
            raise ValueError("RuCommand требует хотя бы одну команду")
        self.commands = {c.lower() for c in commands}
        self.prefix = prefix

    async def __call__(self, message: Message) -> bool | dict:
        text = message.text or message.caption
        if not text:
            return False
        text = text.strip()
        if not text.startswith(self.prefix):
            return False

        first_token = text.split(maxsplit=1)[0]
        command = first_token[len(self.prefix):]
        if "@" in command:
            command = command.split("@", 1)[0]
        if command.lower() not in self.commands:
            return False

        parts = text.split(maxsplit=1)
        args = parts[1].strip() if len(parts) > 1 else ""
        return {"command_args": args}
