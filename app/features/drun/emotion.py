"""Стойкое НАСТРОЕНИЕ друна — эмоция, которая ПЕРЕЖИВАЕТ один ответ.

``mood.py`` уже есть, но он БЕЗ ПАМЯТИ: настроение каждый раз заново выводится
из свежих событий мира и кэшируется на 45с. Поэтому друн не может «остаться
заведённым после того, как его задёргали спамом» или «весь вечер быть на
кураже после джекпота» — между репликами эмоция обнуляется. Живое существо так
не устроено: настроение копится и медленно стекает к норме.

Этот модуль добавляет СТОЙКОЕ эмоциональное состояние на двух осях:

* ``valence``  — от хмурого (-1) до приподнятого (+1): общий заряд;
* ``arousal``  — от вялого/спокойного (0) до взвинченного (1): насколько «на
  нервах», задёрган, заведён.

Состояние лежит в ``ai_settings`` (одна JSONB-строка, без миграции), ЗАТУХАЕТ к
нейтралу по реальному времени с прошлого касания и НАКАПЛИВАЕТСЯ от событий:
наезд/спам → arousal↑, valence↓; джекпот/тепло → valence↑. Чистая математика
вынесена в :func:`decay` / :func:`nudge` (тестируется без БД), persistence —
тонкая обёртка вокруг ``AiSetting``.

Это ортогонально ``mood.py``: тот даёт МГНОВЕННЫЙ снимок мира, а этот — ИНЕРЦИЮ
самого друна. Вместе они красят тон и питают :mod:`variance`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.models import AiSetting

logger = get_logger(__name__)

_KEY = "emotion_state"

# Полураспад осей к нейтралу: за столько часов |valence| и arousal падают вдвое.
# valence инертнее (настроение держится дольше), arousal стекает быстрее (нервы
# отпускают раньше, чем общий настрой).
_VALENCE_HALFLIFE_H = 8.0
_AROUSAL_HALFLIFE_H = 2.5

# Толчки от типовых триггеров: (d_valence, d_arousal). Складываются и зажимаются.
NUDGE_HOSTILE = (-0.18, 0.30)   # наезд/хамство в адрес друна
NUDGE_SPAM = (-0.08, 0.22)      # задёргали обращениями (абуз-режим)
NUDGE_WARM = (0.16, 0.05)       # тепло/благодарность
NUDGE_WIN = (0.22, 0.20)        # джекпот/победа в мире — кураж
NUDGE_CONFLICT = (-0.10, 0.18)  # драки/конфликты в мире — заводят
NUDGE_DEAD = (-0.06, -0.05)     # мёртвый чат — вяло, скучно


@dataclass(frozen=True)
class Emotion:
    """Снимок стойкого настроения друна (после затухания)."""

    valence: float   # -1 (хмурый) .. +1 (приподнятый)
    arousal: float   # 0 (спокоен) .. 1 (взвинчен)

    @property
    def label(self) -> str:
        v, a = self.valence, self.arousal
        if a >= 0.6 and v <= -0.2:
            return "ВЗВИНЧЕН"      # злой и заведённый
        if a >= 0.6 and v >= 0.2:
            return "НА КУРАЖЕ"     # позитивно-возбуждён
        if v <= -0.35:
            return "ХМУРЫЙ"
        if v >= 0.35:
            return "В ДУХЕ"
        if a <= 0.2 and abs(v) < 0.2:
            return "ВЯЛЫЙ"
        return "РОВНЫЙ"

    def directive(self) -> str:
        """Подсказка для промпта: как стойкое настроение красит тон сейчас."""
        lbl = self.label
        text = {
            "ВЗВИНЧЕН": "Ты сейчас НА НЕРВАХ и заведён — реагируешь резче и "
                        "короче обычного, фитиль короткий, легко срываешься в "
                        "колкость. Это пройдёт, но прямо сейчас ты такой.",
            "НА КУРАЖЕ": "Ты на КУРАЖЕ — энергия прёт, тянет жечь, заводить "
                         "движ, рофлить громче. Лови волну.",
            "ХМУРЫЙ": "Настроение так себе, ты хмурый и желчный — больше яда, "
                      "меньше доброты, отвечаешь нехотя.",
            "В ДУХЕ": "Ты в хорошем настроении — добрее обычного, охотнее "
                      "поддержишь и пошутишь без злобы.",
            "ВЯЛЫЙ": "Ты вялый и расслабленный — без надрыва, лень "
                     "разгоняться, отвечай спокойно и коротко.",
            "РОВНЫЙ": "",
        }.get(lbl, "")
        return f"# ТВОЁ СТОЙКОЕ НАСТРОЕНИЕ [{lbl}]: {text}" if text else ""


def decay(
    valence: float, arousal: float, hours: float
) -> tuple[float, float]:
    """Затухание осей к нейтралу за ``hours`` часов реального времени.

    valence → 0, arousal → 0 по экспоненте со своими полураспадами. Чистая
    функция: новое состояние из старого и прошедшего времени.
    """
    if hours <= 0:
        return _clamp_v(valence), _clamp_a(arousal)
    v = valence * (0.5 ** (hours / _VALENCE_HALFLIFE_H))
    a = arousal * (0.5 ** (hours / _AROUSAL_HALFLIFE_H))
    # Подрезаем «хвост» у нуля, чтобы не таскать микрозначения вечно.
    if abs(v) < 0.02:
        v = 0.0
    if a < 0.02:
        a = 0.0
    return _clamp_v(v), _clamp_a(a)


def nudge(
    valence: float, arousal: float, delta: tuple[float, float]
) -> tuple[float, float]:
    """Добавляет толчок (d_valence, d_arousal) к состоянию, с зажатием."""
    dv, da = delta
    return _clamp_v(valence + dv), _clamp_a(arousal + da)


def _clamp_v(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _clamp_a(x: float) -> float:
    return max(0.0, min(1.0, x))


async def get_state(session: AsyncSession) -> Emotion:
    """Текущее стойкое настроение с учётом затухания (нейтрал, если пусто)."""
    try:
        raw = await session.scalar(
            select(AiSetting.value).where(AiSetting.key == _KEY)
        )
        if not isinstance(raw, dict):
            return Emotion(0.0, 0.0)
        v = float(raw.get("valence", 0.0) or 0.0)
        a = float(raw.get("arousal", 0.0) or 0.0)
        ts = raw.get("ts")
        if ts:
            try:
                last = datetime.fromisoformat(ts)
                hours = max(
                    0.0,
                    (datetime.now(timezone.utc) - last).total_seconds() / 3600.0,
                )
                v, a = decay(v, a, hours)
            except (ValueError, TypeError):
                pass
        return Emotion(_clamp_v(v), _clamp_a(a))
    except Exception:  # noqa: BLE001
        logger.debug("emotion get_state failed", exc_info=True)
        return Emotion(0.0, 0.0)


async def apply_nudge(
    session: AsyncSession, delta: tuple[float, float]
) -> Emotion:
    """Затухает текущее состояние до «сейчас», добавляет толчок, сохраняет.

    Коммит — на вызывающем (вписывается в общий поток сессии). Любой сбой
    глотаем: настроение — украшение, оно не должно ронять ответ/джобу.
    """
    try:
        cur = await get_state(session)  # уже с затуханием
        v, a = nudge(cur.valence, cur.arousal, delta)
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {"valence": round(v, 4), "arousal": round(a, 4), "ts": now_iso}
        stmt = (
            pg_insert(AiSetting)
            .values(key=_KEY, value=payload)
            .on_conflict_do_update(
                index_elements=[AiSetting.key], set_={"value": payload}
            )
        )
        await session.execute(stmt)
        return Emotion(v, a)
    except Exception:  # noqa: BLE001
        logger.debug("emotion apply_nudge failed", exc_info=True)
        return Emotion(0.0, 0.0)
