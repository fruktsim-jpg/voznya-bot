"""Эволюционирующее отношение друна к игроку (аффинити).

``attitude.py`` даёт стойку из СТАТИСТИКИ (богач/бомж/боец) — она статична. Этот
модуль добавляет ЖИВОЕ отношение, которое копится из того, КАК человек ведёт
себя С ДРУНОМ: тепло/уважительно общается — аффинити растёт (друг, кореш);
хамит/наезжает на друна — падает (личная вражда). Это переживает один разговор:
кто бесил друна неделю, остаётся врагом, даже если сейчас написал нейтрально.

Храним в ``AiProfile.data["affinity"]`` = {"score": int(-100..100), "ts": iso}.
Без новых таблиц. Обновляется дёшево (без LLM) по сигналам тона сообщения,
с затуханием к нейтралу со временем (старые обиды/симпатии выветриваются).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger

logger = get_logger(__name__)

_MIN, _MAX = -100, 100
# Затухание к нейтралу: за сутки без контакта |score| уменьшается на столько.
_DECAY_PER_DAY = 4

# Сигналы тона сообщения В АДРЕС друна (грубо, без LLM). Подстроки, нижний рег.
_WARM = (
    "спасибо", "спс", "благодар", "красав", "красава", "лучший", "люблю",
    "обожаю", "respect", "респект", "ты крут", "топ", "молодец", "няша",
    "добрый", "помог", "выручил", "обнял", "друг ты", "братан", "братишка",
)
_HOSTILE = (
    "тупой", "тупая", "тупой бот", "заткнись", "затухни", "пошёл нахуй",
    "пошел нахуй", "иди нахуй", "нахуй пошёл", "ненавиж", "бесишь", "бесит",
    "урод", "мудак", "долбоёб", "долбоеб", "дебил", "идиот", "кретин",
    "сдохни", "тварь", "конченый", "конченая", "обнаглел", "достал ты",
    "глупый бот", "тупой ии", "бот тупой",
)


@dataclass
class Affinity:
    """Накопленное личное отношение друна к игроку."""

    score: int  # -100 (личный враг) .. +100 (близкий)

    @property
    def label(self) -> str:
        s = self.score
        if s <= -60:
            return "ЛИЧНЫЙ ВРАГ"
        if s <= -25:
            return "НЕДРУГ"
        if s < 25:
            return "НЕЙТРАЛ"
        if s < 60:
            return "ПРИЯТЕЛЬ"
        return "КОРЕШ"

    @property
    def directive(self) -> str:
        """Как накопленная история отношений красит общение (поверх стойки)."""
        s = self.score
        if s <= -60:
            return (
                "У вас с ним ЛИЧНАЯ ВОЙНА — он не раз тебя оскорблял. Держись "
                "холодно-враждебно, в любой удобный момент можешь приложить, "
                "тёплоты ноль. Но если он РЕЗКО сменил тон и реально мирится — "
                "заметь это, ты живой, а не злопамятный робот."
            )
        if s <= -25:
            return (
                "Он тебя подбешивал, осадок есть. Поддевай острее обычного, "
                "доверия мало, но без открытой войны."
            )
        if s < 25:
            return (
                "Ровные отношения, без истории. Веди по ситуации и по своей "
                "стойке к нему."
            )
        if s < 60:
            return (
                "Вы в неплохих отношениях — он к тебе по-нормальному. Можно "
                "теплее, по-приятельски, подколы беззлобные."
            )
        return (
            "Это твой КОРЕШ — общается с тобой тепло, не раз по-доброму. "
            "Держись как со своим в доску: по-братски, с заботой под слоем "
            "подъёбов, прикрой если что."
        )


def score_sentiment(text: str) -> int:
    """Грубая оценка тона реплики В АДРЕС друна: -2..+2. 0 — нейтрально."""
    low = (text or "").lower()
    warm = sum(1 for w in _WARM if w in low)
    hostile = sum(1 for h in _HOSTILE if h in low)
    raw = warm - hostile
    return max(-2, min(2, raw))


def _decayed(score: int, days: float) -> int:
    """Затухание к нейтралу: |score| уменьшается ~_DECAY_PER_DAY в сутки."""
    if score == 0 or days <= 0:
        return score
    shrink = int(_DECAY_PER_DAY * days)
    if score > 0:
        return max(0, score - shrink)
    return min(0, score + shrink)


def apply_delta(prev_score: int, sentiment: int) -> int:
    """Новое значение аффинити после реплики данного тона.

    Враждебность бьёт сильнее симпатии (обиды копятся быстрее, чем доверие) —
    это делает друна правдоподобно злопамятным, но прощающим при усилии.
    """
    if sentiment == 0:
        return prev_score
    step = sentiment * (5 if sentiment > 0 else 7)
    return max(_MIN, min(_MAX, prev_score + step))


async def get_affinity(session: AsyncSession, user_id: int) -> Affinity:
    """Текущее аффинити игрока (с учётом затухания). Нейтрал, если профиля нет."""
    try:
        from datetime import datetime, timezone

        from app.models import AiProfile

        prof = await session.get(AiProfile, user_id)
        if prof is None:
            return Affinity(0)
        aff = (prof.data or {}).get("affinity") or {}
        score = int(aff.get("score", 0) or 0)
        ts = aff.get("ts")
        if ts:
            try:
                last = datetime.fromisoformat(ts)
                now = datetime.now(timezone.utc)
                days = max(0.0, (now - last).total_seconds() / 86400)
                score = _decayed(score, days)
            except (ValueError, TypeError):
                pass
        return Affinity(max(_MIN, min(_MAX, score)))
    except Exception:  # noqa: BLE001
        logger.debug("get_affinity failed", exc_info=True)
        return Affinity(0)


async def record_interaction(
    session: AsyncSession, user_id: int, text: str
) -> None:
    """Обновляет аффinity по тону реплики игрока в адрес друна (дёшево, без LLM).

    Зовётся на каждый прямой ответ. Применяет затухание от прошлого контакта,
    затем дельту тона. Профиль создаётся лениво (если игрок ещё без портрета),
    чтобы первая же тёплая/злая реплика начала копить отношение. Коммит — на
    вызывающем (в общем потоке respond).
    """
    sentiment = score_sentiment(text)
    try:
        from datetime import datetime, timezone

        from app.models import AiProfile

        prof = await session.get(AiProfile, user_id)
        prev = 0
        last_days = 0.0
        if prof is not None:
            aff = (prof.data or {}).get("affinity") or {}
            prev = int(aff.get("score", 0) or 0)
            ts = aff.get("ts")
            if ts:
                try:
                    last = datetime.fromisoformat(ts)
                    last_days = max(
                        0.0,
                        (datetime.now(timezone.utc) - last).total_seconds() / 86400,
                    )
                except (ValueError, TypeError):
                    pass
        # Нечего записывать и нет профиля — не плодим пустые строки.
        if prof is None and sentiment == 0:
            return
        new_score = apply_delta(_decayed(prev, last_days), sentiment)
        now_iso = datetime.now(timezone.utc).isoformat()
        if prof is None:
            prof = AiProfile(user_id=user_id, data={})
            session.add(prof)
        data = dict(prof.data or {})
        data["affinity"] = {"score": new_score, "ts": now_iso}
        prof.data = data
    except Exception:  # noqa: BLE001
        logger.debug("record_interaction failed", exc_info=True)
