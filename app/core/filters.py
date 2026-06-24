"""Фильтры aiogram для команд на русском языке.

Telegram не всегда размечает кириллические команды (например, «/ферма»)
как сущности типа ``bot_command``. Поэтому стандартный фильтр Command
ненадёжен для русских команд. Здесь — собственный фильтр, который разбирает
текст сообщения вручную и работает с любыми командами.
"""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message


_FREE_TEXT_COMMANDS = {
    # Commands that intentionally take arbitrary natural-language tails.
    "друн", "drun",
    "бан", "ban", "мут", "mute", "варн", "warn", "пред",
    "кик", "kick", "инфо", "info", "выдать", "give", "забрать", "take",
}


_ARG_KIND_BY_COMMAND = {
    # numeric amount/page/id
    "бой": "numeric", "duel": "numeric", "дуэль": "numeric", "дуэлька": "numeric",
    "казино": "numeric", "casino": "numeric",
    "кейс": "numeric", "case": "numeric", "открыть": "numeric", "open": "numeric",
    "топ": "numeric", "top": "numeric", "инвентарь": "numeric", "инв": "numeric",
    "рюкзак": "numeric", "inventory": "numeric", "inv": "numeric",
    "gifts_done": "numeric", "gifts_retry": "numeric", "gifts_refund": "numeric",
    # target-like args: @username, reply-driven commands, ids, short names
    "профиль": "target", "profile": "target", "проф": "target",
    "ачивки": "target", "achievements": "target", "ачивы": "target", "достижения": "target",
    "реп": "target", "репутация": "target", "rep": "target", "reputation": "target",
    "жениться": "target", "marry": "target", "свадьба": "target", "предложение": "target",
    "осеменить": "target", "изнасиловать": "target", "выебать": "target",
    "наплюхать": "target", "сделать_беременной": "target",
    "gifts_setid": "target",
    # fixed subcommands / one-token args
    "кто": "exact_ty",
    "развод": "empty", "divorce": "empty", "развестись": "empty", "разрыв": "empty",
    "расстаться": "empty",
}

_KNOWN_COMMANDS = frozenset(_ARG_KIND_BY_COMMAND) | _FREE_TEXT_COMMANDS | {
    # empty/no-arg public commands
    "ферма", "farm", "фарм", "го", "accept", "go", "кейсы", "cases",
    "магазин", "shop", "подарки", "gifts", "моиподарки", "mygifts",
    "снять", "claim", "клад", "топнеделя", "weekly", "семьи", "families",
    "браки", "свадьбы", "топммр", "topmmr", "топреп", "toprep", "сезон",
    "season", "бонус", "daily", "дейли", "миссии", "missions", "топсезон",
    "topseason", "баланс", "balance", "бал", "деньги", "кошелёк",
    "кошелек", "бабки", "помощь", "help", "старт", "start", "команды",
    "меню", "брак", "marriage", "жена", "муж", "пидор", "pidor", "пара",
    "couple", "para",
}


def _arg_allowed(command: str, args: str) -> bool:
    """Reject accidental no-slash command matches in normal sentences.

    Slash commands keep the old permissive behavior. For bare Russian aliases we
    only allow empty tails or narrow argument shapes; otherwise messages like
    "топ погода сегодня" should remain ordinary chat, not delete themselves as a
    leaderboard command.
    """
    command = command.lower()
    tail = (args or "").strip()
    if not tail:
        return True
    if command in _FREE_TEXT_COMMANDS:
        return True
    kind = _ARG_KIND_BY_COMMAND.get(command)
    if kind == "numeric":
        parts = tail.split()
        return len(parts) <= 2 and any(part.lstrip("+-").isdigit() for part in parts)
    if kind == "target":
        first = tail.split(maxsplit=1)[0]
        if first.startswith("@") or first.lstrip("+-").isdigit():
            return True
        return len(tail.split()) == 1 and 2 <= len(first) <= 32
    if kind == "exact_ty":
        return tail.lower() == "ты"
    if kind == "empty":
        return False
    return False


def looks_like_known_command(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    first_token = raw.split(maxsplit=1)[0]
    explicit = first_token.startswith("/")
    command = first_token[1:] if explicit else first_token
    if "@" in command:
        command = command.split("@", 1)[0]
    command = command.lower()
    if explicit:
        return bool(command)
    if command not in _KNOWN_COMMANDS:
        return False
    parts = raw.split(maxsplit=1)
    args = parts[1].strip() if len(parts) > 1 else ""
    return _arg_allowed(command, args)


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
        explicit = first_token.startswith("/")
        if explicit:
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
        if not explicit and not _arg_allowed(command, args):
            return False
        return {"command_args": args}
