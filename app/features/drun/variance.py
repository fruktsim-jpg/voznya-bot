"""Вариативность реплик друна — лекарство от «однокнопочного» бота.

Расследование показало: однообразие ответов рождается НЕ в промптах и не в
качестве модели, а в АРХИТЕКТУРЕ — каждый ответ идёт по одному и тому же
пайплайну с одной и той же температурой и статичной инструкцией «будь разным».
Статичная инструкция всегда регрессирует к среднему: модель видит её каждый раз
и каждый раз отвечает «средне-дерзко, средней длины». Живой человек так себя не
ведёт — иногда он рубит одно слово, иногда выдаёт простыню, иногда саркастичен
до яда, иногда вял и краток.

Этот модуль вводит ЯВНУЮ дисперсию: на каждый ответ собирается
:class:`StyleProfile` — набор осей (длина, выложенность, сарказм, агрессия,
тёплость) со СЛУЧАЙНЫМ, но СМЕЩЁННЫМ распределением. Смещение задают уже
посчитанные сигналы (намерение perceive, настроение, аффинити, сила позыва),
рандом даёт разброс вокруг смещения. Профиль конвертируется в:

* короткую директиву для промпта (КАК звучать именно сейчас);
* override температуры сэмплинга (живость на уровне декодера, а не только слов).

Чистая функция, без БД и LLM: дёшево на горячем пути и тестируемо.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum


class Length(str, Enum):
    """Целевая длина реплики (разброс — главный признак живости)."""

    TERSE = "terse"      # 1-4 слова, рубленый, иногда просто реакция
    SHORT = "short"      # одна живая фраза
    MEDIUM = "medium"    # 2-3 фразы, нормальный ответ
    LONG = "long"        # развёрнуто, со своим мнением (редко)


# Человекочитаемые указания по длине (идут в промпт как жёсткая рамка — модель
# плохо «чувствует» длину сама, ей нужен конкретный потолок).
_LENGTH_HINT: dict[Length, str] = {
    Length.TERSE: (
        "ОТВЕТ — РУБЛЕНЫЙ: максимум 1-4 слова или один смайл/междометие. "
        "Иногда достаточно односложной реакции («ну да», «слабо», «лол»). "
        "НЕ разворачивай мысль."
    ),
    Length.SHORT: "Ответ — ОДНА живая короткая фраза. Без второй мысли.",
    Length.MEDIUM: "Ответ — 2-3 фразы, нормально, без простыни.",
    Length.LONG: (
        "Сейчас тебе есть что сказать — можешь развернуться на несколько фраз, "
        "со своим мнением и заходом. Но не лей воду."
    ),
}

# Жёсткий потолок символов под каждую длину — страховка от простыни на TERSE
# (модель любит игнорировать словесные рамки). Используется как max_chars фильтра.
_LENGTH_CAP: dict[Length, int] = {
    Length.TERSE: 80,
    Length.SHORT: 200,
    Length.MEDIUM: 480,
    Length.LONG: 1100,
}


@dataclass(frozen=True)
class StyleProfile:
    """Оси стиля одной конкретной реплики (0..1, кроме длины)."""

    length: Length
    effort: float      # 0 (лень, отмазка) .. 1 (вложился, постарался)
    sarcasm: float     # 0 (прямо) .. 1 (язвительно, с подтекстом)
    aggression: float  # 0 (мирно) .. 1 (жёстко, на добивание)
    warmth: float      # 0 (холодно) .. 1 (тепло, по-братски)
    temperature: float  # override сэмплинга провайдера

    @property
    def max_chars(self) -> int:
        """Потолок длины ответа для пост-фильтра."""
        return _LENGTH_CAP[self.length]

    def directive(self) -> str:
        """Инструкция-рамка для промпта: как звучать ИМЕННО в этой реплике.

        Это НЕ «будь разным вообще» (такая инструкция мертва), а конкретная
        разнарядка на ОДИН ответ: вот такая длина, вот столько яда, вот столько
        тепла. В сумме по многим ответам это и даёт живую дисперсию.
        """
        bits = [_LENGTH_HINT[self.length]]
        if self.effort < 0.3:
            bits.append(
                "Тебе сейчас лень вкладываться — ответь нехотя, вполсилы, "
                "можешь отмахнуться."
            )
        elif self.effort > 0.75:
            bits.append("Вложись в реплику — придумай свежий заход, не на отвали.")
        if self.aggression > 0.7:
            bits.append("Тон ЖЁСТКИЙ: режь прямо, на добивание, без смягчений.")
        elif self.aggression < 0.25 and self.warmth < 0.5:
            bits.append("Тон спокойный, без наезда.")
        if self.sarcasm > 0.65:
            bits.append("Подпусти ЯДА и сарказма — говори с подтекстом, а не в лоб.")
        if self.warmth > 0.7:
            bits.append("С теплом — по-братски, по-доброму, своему человеку.")
        return "# КАК ЗВУЧАТЬ В ЭТОЙ РЕПЛИКЕ (разнарядка на сейчас):\n- " + "\n- ".join(
            bits
        )


# --- Смещения по намерению (perceive.Intent.value → центр осей) ---------------
# Каждое намерение тянет оси в свою сторону; рандом потом добавляет разброс.
# Поля: (effort, sarcasm, aggression, warmth).
_INTENT_BIAS: dict[str, tuple[float, float, float, float]] = {
    "roast":   (0.6, 0.75, 0.75, 0.1),
    "hype":    (0.7, 0.35, 0.2, 0.55),
    "support": (0.55, 0.2, 0.1, 0.8),
    "stir":    (0.6, 0.6, 0.45, 0.3),
    "comment": (0.45, 0.5, 0.35, 0.35),
    "silent":  (0.4, 0.5, 0.4, 0.4),
}

# Настроения, повышающие агрессию/сарказм, и те, что добавляют тепла/куража.
_MOOD_AGGRO = {"angry", "chaotic", "suspicious"}
_MOOD_WARM = {"celebratory", "amused", "excited"}
_MOOD_LOW_EFFORT = {"disappointed"}


def _jitter(center: float, spread: float, rng: random.Random) -> float:
    """Случайное значение вокруг центра, зажатое в [0,1].

    Треугольное распределение (mode=center) — значения тяготеют к смещению, но
    дают хвосты: иногда друн ведёт себя нетипично для своего намерения, как
    живой человек в настроении «не как обычно».
    """
    lo = max(0.0, center - spread)
    hi = min(1.0, center + spread)
    if hi <= lo:
        return max(0.0, min(1.0, center))
    return rng.triangular(lo, hi, max(lo, min(hi, center)))


def _pick_length(
    *,
    intent_kind: str | None,
    urge: float,
    addressed: bool,
    effort: float,
    rng: random.Random,
) -> Length:
    """Выбор длины со смещённым рандомом.

    Базовое распределение тяготеет к коротким ответам (живой чат — это в
    основном короткие реплики), но намерение/повод/выложенность сдвигают шансы:
    наезд и поддержка чаще требуют пары фраз, ленивое настроение тянет в рубку,
    прямое обращение слегка удлиняет (человеку отвечают, а не воздуху).
    """
    # Веса [terse, short, medium, long].
    w = [0.28, 0.42, 0.22, 0.08]
    ik = (intent_kind or "").lower()
    if ik == "roast":
        w = [0.34, 0.4, 0.2, 0.06]      # подъёб часто короткий и злой
    elif ik in ("support", "comment"):
        w = [0.12, 0.4, 0.34, 0.14]     # тут уместнее развернуться
    elif ik == "hype":
        w = [0.3, 0.44, 0.2, 0.06]
    elif ik == "stir":
        w = [0.2, 0.45, 0.25, 0.1]
    # Сильный позыв/высокая выложенность сдвигают вес к длинным.
    push = (urge * 0.5) + (effort * 0.5)
    if push > 0.7:
        w = [w[0] * 0.5, w[1], w[2] * 1.3, w[3] * 2.0]
    elif push < 0.35:
        w = [w[0] * 1.6, w[1] * 1.1, w[2] * 0.6, w[3] * 0.3]
    if not addressed:
        # Неадресный вкид — короче (не лезь с лекцией без спроса).
        w = [w[0] * 1.4, w[1] * 1.1, w[2] * 0.7, w[3] * 0.4]
    total = sum(w) or 1.0
    r = rng.random() * total
    acc = 0.0
    for length, weight in zip(
        (Length.TERSE, Length.SHORT, Length.MEDIUM, Length.LONG), w
    ):
        acc += weight
        if r <= acc:
            return length
    return Length.SHORT


def build_style(
    *,
    intent_kind: str | None = None,
    mood_label: str | None = None,
    mood_intensity: int = 1,
    affinity_score: int = 0,
    urge: float = 0.0,
    addressed: bool = True,
    base_temperature: float = 0.9,
    op_annoyance: float = 50.0,
    op_respect: float = 50.0,
    op_entertainment: float = 50.0,
    op_trust: float = 50.0,
    rng: random.Random | None = None,
) -> StyleProfile:
    """Собирает стиль одной реплики из сигналов + смещённого рандома.

    :param intent_kind: код намерения из perceive (roast/hype/support/...).
    :param mood_label: текущее настроение друна (mood.MOOD_*).
    :param mood_intensity: сила настроения 1..3 — масштабирует его влияние.
    :param affinity_score: накопленное личное отношение к собеседнику (-100..100).
    :param urge: сила позыва вмешаться (0..1) — питает длину/выложенность.
    :param addressed: к друну обратились напрямую (иначе спонтанный вкид).
    :param base_temperature: температура из конфига — вокруг неё джиттерим.
    :param op_annoyance: ось мнения «раздражает» (0..100, нейтрал 50) — выше
        тянет в агрессию/сарказм И расширяет разброс (склонность ПЕРЕГИБАТЬ).
    :param op_respect: ось «уважение» — выше смягчает агрессию (с уважаемым
        грубят реже), ниже развязывает.
    :param op_entertainment: ось «с ним весело» — выше включает игривый яд
        (любит подъёбывать по-доброму): сарказм↑ и тепло↑ одновременно.
    :param op_trust: ось «доверие» — низкое добавляет колкости/скепсиса.
    :param rng: источник случайности (инъекция для тестов).
    """
    rng = rng or random
    eff_c, sar_c, agg_c, warm_c = _INTENT_BIAS.get(
        (intent_kind or "").lower(), _INTENT_BIAS["comment"]
    )

    # Настроение двигает центры осей (масштаб — по интенсивности).
    m = (mood_label or "").lower()
    scale = 0.12 * max(1, min(3, mood_intensity))
    if m in _MOOD_AGGRO:
        agg_c += scale
        sar_c += scale * 0.6
        warm_c -= scale
    if m in _MOOD_WARM:
        warm_c += scale
        agg_c -= scale * 0.6
    if m in _MOOD_LOW_EFFORT:
        eff_c -= scale

    # Аффинити: кореша — теплее и мягче; личные враги — злее и язвительнее.
    aff = max(-100, min(100, affinity_score)) / 100.0
    warm_c += aff * 0.35
    agg_c -= aff * 0.3
    sar_c -= aff * 0.15 if aff > 0 else aff * 0.2  # враг — больше яда

    # СЛОЖИВШЕЕСЯ МНЕНИЕ (LEAP-4): многомерное отношение, копившееся неделями,
    # красит стиль поверх сиюминутных аффинити/настроения. В отличие от них оно
    # инерционно — постоянная «личностная» окраска общения именно с этим
    # человеком. Нормируем оси в [-0.5..0.5] вокруг нейтрала 50.
    ann = (max(0.0, min(100.0, op_annoyance)) - 50.0) / 100.0
    resp = (max(0.0, min(100.0, op_respect)) - 50.0) / 100.0
    ent = (max(0.0, min(100.0, op_entertainment)) - 50.0) / 100.0
    tru = (max(0.0, min(100.0, op_trust)) - 50.0) / 100.0
    agg_c += ann * 0.5          # бесит → злее
    sar_c += ann * 0.4
    agg_c -= resp * 0.4         # уважаемому грубят реже
    sar_c += ent * 0.35         # с весёлым — игривый яд…
    warm_c += ent * 0.25        # …но по-доброму (яд + тепло = подъёб своего)
    sar_c -= tru * 0.25         # не доверяешь → больше скепсиса/колкости

    # Джиттер вокруг смещённых центров (широкий разброс = живость). Сильное
    # раздражение РАСШИРЯЕТ разброс агрессии — это «склонность перегибать»:
    # с тем, кто бесит, друн иногда реагирует непропорционально резко.
    agg_spread = 0.32 + max(0.0, ann) * 0.3
    effort = _jitter(eff_c, 0.3, rng)
    sarcasm = _jitter(sar_c, 0.32, rng)
    aggression = _jitter(agg_c, agg_spread, rng)
    warmth = _jitter(warm_c, 0.3, rng)

    length = _pick_length(
        intent_kind=intent_kind, urge=urge, addressed=addressed,
        effort=effort, rng=rng,
    )

    # Температура: вокруг базовой ±0.18, плюс надбавка за хаос/сарказм и спад
    # за низкую выложенность. Зажимаем в разумный коридор для болтливой модели.
    temp = base_temperature + rng.uniform(-0.18, 0.18)
    if m == "chaotic":
        temp += 0.1
    temp += (sarcasm - 0.5) * 0.1
    temp -= (0.5 - effort) * 0.12
    temp = max(0.55, min(1.25, temp))

    return StyleProfile(
        length=length,
        effort=round(effort, 3),
        sarcasm=round(sarcasm, 3),
        aggression=round(aggression, 3),
        warmth=round(warmth, 3),
        temperature=round(temp, 3),
    )
