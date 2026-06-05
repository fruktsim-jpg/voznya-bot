"""Утилиты: работа со временем, игровыми датами и форматированием.


Все «игровые» расчёты дат делаются в часовом поясе из настроек
(по умолчанию Europe/Amsterdam).
"""

from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings


def get_tz() -> ZoneInfo:
    """Возвращает игровой часовой пояс из настроек."""
    return ZoneInfo(get_settings().timezone)


def now_local() -> datetime:
    """Текущее время в игровом часовом поясе (timezone-aware)."""
    return datetime.now(get_tz())


def now_utc() -> datetime:
    """Текущее время в UTC (timezone-aware)."""
    return datetime.now(ZoneInfo("UTC"))


def to_local(dt: datetime) -> datetime:
    """Приводит произвольный datetime к игровому часовому поясу.

    Наивные значения (без tzinfo) считаются UTC — так их хранит БД.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(get_tz())


def nomination_date(moment: datetime | None = None) -> date:
    """Возвращает «игровую дату» номинаций (Пидор/Пара дня).

    Сутки номинаций начинаются в ``NOMINATION_RESET_HOUR`` (по умолчанию 0 —
    то есть в 00:00). При reset_hour=0 игровая дата совпадает с календарной,
    поэтому номинации обновляются ровно в полночь. Сам выбор «ленивый»: нового
    победителя определяет первый вызов команды после смены даты.
    """

    if moment is None:
        moment = now_local()
    else:
        moment = to_local(moment)
    reset_hour = get_settings().nomination_reset_hour
    shifted = moment - timedelta(hours=reset_hour)
    return shifted.date()


def farm_day(moment: datetime | None = None) -> date:
    """Возвращает календарную дату для подсчёта серии фермы (граница — полночь)."""
    if moment is None:
        moment = now_local()
    else:
        moment = to_local(moment)
    return moment.date()


def format_cooldown(seconds: float) -> str:
    """Форматирует оставшееся время кулдауна (часы/минуты/секунды)."""
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if secs or not parts:
        parts.append(f"{secs} сек")
    return " ".join(parts)


def format_marriage_duration(since: datetime, until: datetime | None = None) -> str:
    """Форматирует длительность брака в днях, часах и минутах."""
    start = to_local(since)
    end = to_local(until) if until is not None else now_local()
    delta = end - start
    if delta.total_seconds() < 0:
        delta = timedelta(0)
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes = remainder // 60
    return f"{days} дн. {hours} ч. {minutes} мин."


def format_marriage_duration_days(since: datetime, until: datetime | None = None) -> str:
    """Форматирует длительность брака только в днях."""
    start = to_local(since)
    end = to_local(until) if until is not None else now_local()
    delta = end - start
    if delta.total_seconds() < 0:
        delta = timedelta(0)
    days = delta.days
    return f"{days} дн." if days > 0 else "менее дня"


def escape(text: str | None) -> str:
    """Экранирует текст для HTML-режима Telegram."""
    if not text:
        return ""
    return html.escape(text, quote=False)


_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def place_marker(place: int) -> str:
    """Возвращает медаль для топ-3 или «N.» для остальных мест."""
    return _MEDALS.get(place, f"{place}.")


def progress_bar(ratio: float, width: int = 12) -> str:
    """Рисует текстовый прогресс-бар из закрашенных/пустых блоков."""
    ratio = max(0.0, min(1.0, ratio))
    filled = round(ratio * width)
    return "▰" * filled + "▱" * (width - filled)


def mention(user_id: int, name: str | None, username: str | None = None) -> str:
    """Создаёт HTML-упоминание пользователя.

    Используется tg://user?id=... — работает даже без username.
    """
    display = escape(name) or (f"@{escape(username)}" if username else "Игрок")
    return f'<a href="tg://user?id={user_id}">{display}</a>'
