"""Эвристика пола по репликам игрока — фолбэк, когда LLM не уверен.

Друн часто лажает с родом (34% профилей в проде имели gender='unknown'), и
особенно обидно ошибаться с девушками. LLM-портрет иногда возвращает 'unknown'
(мало явных маркеров), но русский язык выдаёт пол грамматически: глаголы
прошедшего времени 1-го лица («я сделалА» → ж, «я устал» → м), причастия,
обращения к себе. Эта чистая эвристика добирает пол из СОБСТВЕННЫХ реплик
человека, когда LLM спасовал. Только сигналы от первого лица — чужой род в
пересказе («она сказала») не считаем.

Возвращает 'male' / 'female' / 'unknown'. Консервативна: при слабом или
противоречивом сигнале — 'unknown' (лучше нейтрально, чем неверно).
"""
from __future__ import annotations

import re

# Явные самоназвания пола (сильный сигнал).
_FEMALE_WORDS = (
    "я девушка", "я девочка", "я женщина", "я мама", "как девушка",
    "я жена", "я её", "я сама ", "я одна осталась",
)
_MALE_WORDS = (
    "я парень", "я мужик", "я мужчина", "я пацан", "я папа", "я муж ",
    "я сам ", "я один остался",
)

# Глаголы прошедшего времени от 1-го лица. Требуем местоимение «я» НЕДАЛЕКО
# (до 2 слов между «я» и глаголом: «я вчера сделалА»), чтобы ловить живую речь,
# но не цеплять чужой род из пересказа. Женский: «…ла/…лась». Мужской: «…л/…лся»,
# но НЕ «…ла» (negative lookbehind), иначе «сделала» попадёт и в мужской паттерн.
_NEAR = r"\bя\s+(?:[а-яё]+\s+){0,2}"
_FEM_VERB = re.compile(_NEAR + r"[а-яё]+(?:ла|лась|лесь)\b", re.IGNORECASE)
_MASC_VERB = re.compile(_NEAR + r"[а-яё]+л(?:ся)?\b(?<!ла)", re.IGNORECASE)

# Сколько перевес сигналов нужен, чтобы решиться (анти-шум: одиночный «спалась»
# vs устойчивая картина). Требуем и минимум, и явное преобладание.
_MIN_SIGNALS = 2
_MARGIN = 2


def _count(patterns_text: str, fem_words, masc_words) -> tuple[int, int]:
    low = patterns_text.lower()
    fem = sum(low.count(w) for w in fem_words)
    masc = sum(low.count(w) for w in masc_words)
    fem += len(_FEM_VERB.findall(low))
    masc += len(_MASC_VERB.findall(low))
    return fem, masc


def infer_gender(messages: list[str]) -> str:
    """Грубо определяет пол по СОБСТВЕННЫМ репликам игрока. 'unknown' при сомнении.

    ``messages`` — тексты реплик самого игрока. Чистая функция, без БД/LLM.
    """
    if not messages:
        return "unknown"
    blob = "\n".join(m for m in messages if m)
    if not blob.strip():
        return "unknown"
    low = blob.lower()
    # Явное самоназвание пола — решающий сигнал (даже одно упоминание).
    if any(w in low for w in _FEMALE_WORDS):
        if not any(w in low for w in _MALE_WORDS):
            return "female"
    elif any(w in low for w in _MALE_WORDS):
        return "male"
    # Иначе — по глагольной статистике с порогом и перевесом.
    fem, masc = _count(blob, _FEMALE_WORDS, _MALE_WORDS)
    if fem >= _MIN_SIGNALS and fem - masc >= _MARGIN:
        return "female"
    if masc >= _MIN_SIGNALS and masc - fem >= _MARGIN:
        return "male"
    return "unknown"
