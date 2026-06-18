"""Умный анти-повтор: вычисляет, что друн зажевал, и прямо это запрещает.

Простой показ последних реплик («не повторяйся») слабо работает: модель всё
равно долбит одни зачины и обороты. Здесь анализируем последние СОБСТВЕННЫЕ
реплики друна и достаём:

* частые ЗАЧИНЫ (первые 1-2 слова) — друн любит начинать одинаково;
* частые n-граммы (2-3 слова) — заезженные обороты/слова-паразиты.

Возвращаем явный «чёрный список», который кладём в контекст с запретом. Это
бьёт по реальной причине однообразия, а не по симптому.
"""

from __future__ import annotations

import re
from collections import Counter

# Слишком частые служебные слова — не считаем их «оборотами».
_STOP = frozenset(
    {
        "и", "а", "но", "да", "не", "ну", "вот", "что", "как", "это", "так",
        "же", "бы", "ли", "то", "в", "на", "с", "у", "за", "по", "о", "от",
        "ты", "я", "он", "она", "они", "мы", "вы", "тут", "там", "ещё", "еще",
        "уже", "вообще", "типа", "короче", "блять", "бля",
    }
)
_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def _openers(posts: list[str]) -> Counter[str]:
    """Зачины: первые 1-2 значимых слова каждой реплики."""
    cnt: Counter[str] = Counter()
    for p in posts:
        toks = _tokens(p)
        if not toks:
            continue
        cnt[toks[0]] += 1
        if len(toks) >= 2:
            cnt[f"{toks[0]} {toks[1]}"] += 1
    return cnt


def _ngrams(posts: list[str], n: int) -> Counter[str]:
    """Частые n-граммы (обороты), без чисто служебных."""
    cnt: Counter[str] = Counter()
    for p in posts:
        toks = _tokens(p)
        for i in range(len(toks) - n + 1):
            gram = toks[i : i + n]
            if all(t in _STOP for t in gram):
                continue
            cnt[" ".join(gram)] += 1
    return cnt


def overused(posts: list[str]) -> dict[str, list[str]]:
    """Возвращает {openers:[...], phrases:[...], topics:[...]} — что зажевано."""
    if not posts:
        return {"openers": [], "phrases": [], "topics": []}
    op = _openers(posts)
    banned_openers = [w for w, c in op.most_common(8) if c >= 2]

    phrases: Counter[str] = Counter()
    phrases.update(_ngrams(posts, 2))
    phrases.update(_ngrams(posts, 3))
    banned_phrases = [g for g, c in phrases.most_common(20) if c >= 2]
    # Длинные фразы информативнее коротких — оставим до 10 самых тяжёлых.
    banned_phrases.sort(key=lambda g: (-len(g), g))

    # ТЕМЫ: значимые слова, к которым друн прилип (упоминал в 3+ из последних
    # реплик). Это ловит «обсасывание одного и того же» — когда он не повторяет
    # дословно, но долбит одну тему/мишень (ник, предмет, сюжет). Группируем по
    # грубому стему (первые 5 букв), чтобы русская морфология не дробила тему:
    # «зарплата»/«зарплату»/«зарплате» — это одно и то же залипание.
    def _stem(w: str) -> str:
        return w[:5]

    topic_docs: list[dict[str, str]] = []
    for p in posts:
        # stem -> репрезентативная (самая короткая) словоформа для показа.
        forms: dict[str, str] = {}
        for t in _tokens(p):
            if len(t) >= 4 and t not in _STOP:
                st = _stem(t)
                if st not in forms or len(t) < len(forms[st]):
                    forms[st] = t
        topic_docs.append(forms)
    topic_df: Counter[str] = Counter()
    display: dict[str, str] = {}
    for forms in topic_docs:
        for st, word in forms.items():
            topic_df[st] += 1  # в скольких репликах встретилась тема (по стему)
            if st not in display or len(word) < len(display[st]):
                display[st] = word
    sticky_topics = [display[st] for st, c in topic_df.most_common(12) if c >= 3]

    return {
        "openers": banned_openers[:6],
        "phrases": banned_phrases[:10],
        "topics": sticky_topics[:6],
    }


def render_block(posts: list[str]) -> str:
    """Текстовый блок с явным запретом зажёванного (для контекста)."""
    data = overused(posts)
    if not data["openers"] and not data["phrases"] and not data["topics"]:
        return ""
    lines = ["# СТОП-СЛОВА (ты их уже задолбал, НЕ используй в этом ответе):"]
    if data["openers"]:
        lines.append("Запрещённые зачины: " + ", ".join(data["openers"]))
    if data["phrases"]:
        lines.append("Запрещённые обороты: " + "; ".join(data["phrases"]))
    if data["topics"]:
        lines.append(
            "ЗАЖЁВАННЫЕ ТЕМЫ (ты прилип к ним и долбишь по кругу — СМЕНИ "
            "пластинку, не возвращайся к ним): " + ", ".join(data["topics"])
        )
    lines.append("Начни иначе, скажи это другими словами и про другое.")
    return "\n".join(lines)
