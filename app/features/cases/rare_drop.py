"""Глобальные уведомления о редких дропах (Release 2.2).

Когда кто-то выбивает действительно редкое — это событие сообщества. Бот шлёт
сообщение в общий чат (``settings.chat_id``), а сайт показывает то же событие в
live feed / на главной / в профиле (социальное доказательство).

Чтобы НЕ спамить, анонс срабатывает только если выполнено хотя бы одно из
условий (все пороги в конфиге, любой можно отключить):
* джекпот (``is_jackpot``);
* реальный Telegram Gift / Premium (``reward_kind == 'tg_gift'``);
* шанс выпадения ниже ``rare_drop_chance_pct`` процентов;
* стоимость дропа не ниже ``rare_drop_min_value`` ешек.

Функция best-effort: никогда не бросает наружу — проблема с анонсом не должна
ломать уже зафиксированное открытие кейса.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot

from app.config import get_settings
from app.core.logger import get_logger
from app.core.money import money

logger = get_logger(__name__)


@dataclass(frozen=True)
class RareDrop:
    """Данные выпавшего дропа для проверки порога и рендера анонса."""

    user_mention: str          # @username или имя игрока (готово к показу)
    case_name: str
    item_name: str             # человекочитаемое имя награды (не код)
    is_jackpot: bool
    is_gift: bool              # реальный Telegram Gift / Premium
    is_premium: bool           # подмножество is_gift: именно Premium
    value_eshki: int | None    # стоимость в ешках (если известна)
    chance_pct: float | None   # шанс выпадения в процентах (если известен)


def is_rare(drop: RareDrop) -> bool:
    """Решает, достоин ли дроп глобального анонса (по порогам из конфига)."""
    s = get_settings()
    if not s.rare_drop_announce_enabled:
        return False
    if s.rare_drop_announce_jackpot and drop.is_jackpot:
        return True
    if s.rare_drop_announce_gift and drop.is_gift:
        return True
    if (
        s.rare_drop_chance_pct > 0
        and drop.chance_pct is not None
        and drop.chance_pct < s.rare_drop_chance_pct
    ):
        return True
    if (
        s.rare_drop_min_value > 0
        and drop.value_eshki is not None
        and drop.value_eshki >= s.rare_drop_min_value
    ):
        return True
    return False


def render(drop: RareDrop) -> str:
    """Формирует текст анонса. Premium/лимитки получают свой акцент."""
    # Заголовок: Premium и джекпот — отдельные акценты, иначе общий «крупная находка».
    if drop.is_premium:
        header = "⭐ <b>PREMIUM ВЫПАЛ!</b>"
    elif drop.is_jackpot:
        header = "💎 <b>ДЖЕКПОТ!</b>"
    elif drop.is_gift:
        header = "🎁 <b>РЕДКИЙ ПОДАРОК!</b>"
    else:
        header = "🎉 <b>КРУПНАЯ НАХОДКА</b>"

    lines = [
        header,
        "",
        f"Игрок: {drop.user_mention}",
        f"Кейс: {drop.case_name}",
        f"Предмет: <b>{drop.item_name}</b>",
    ]
    if drop.value_eshki:
        lines.append(f"Стоимость: {money(drop.value_eshki)}")
    if drop.chance_pct is not None:
        # Мелкие шансы показываем точнее (0.05%), крупные — без хвоста.
        pct = (
            f"{drop.chance_pct:.2f}%"
            if drop.chance_pct < 1
            else f"{drop.chance_pct:.1f}%"
        )
        lines.append(f"Шанс: {pct}")
    return "\n".join(lines)


async def announce_if_rare(bot: Bot, drop: RareDrop) -> bool:
    """Если дроп редкий — шлёт анонс в общий чат. Возвращает, был ли анонс.

    Best-effort: любые ошибки логируются и гасятся (анонс не критичен).
    """
    try:
        if not is_rare(drop):
            return False
        chat_id = get_settings().chat_id
        await bot.send_message(chat_id, render(drop))
        return True
    except Exception:  # noqa: BLE001
        logger.warning("rare drop announce failed", exc_info=True)
        return False
