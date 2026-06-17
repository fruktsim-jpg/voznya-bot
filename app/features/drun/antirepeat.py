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
    """Возвращает {openers:[...], phrases:[...]} — что зажевано и под запретом."""
    if not posts:
        return {"openers": [], "phrases": []}
    op = _openers(posts)
    banned_openers = [w for w, c in op.most_common(8) if c >= 2]

    phrases: Counter[str] = Counter()
    phrases.update(_ngrams(posts, 2))
    phrases.update(_ngrams(posts, 3))
    banned_phrases = [g for g, c in phrases.most_common(20) if c >= 2]
    # Длинные фразы информативнее коротких — оставим до 10 самых тяжёлых.
    banned_phrases.sort(key=lambda g: (-len(g), g))
    return {"openers": banned_openers[:6], "phrases": banned_phrases[:10]}


def render_block(posts: list[str]) -> str:
    """Текстовый блок с явным запретом зажёванного (для контекста)."""
    data = overused(posts)
    if not data["openers"] and not data["phrases"]:
        return ""
    lines = ["# СТОП-СЛОВА (ты их уже задолбал, НЕ используй в этом ответе):"]
    if data["openers"]:
        lines.append("Запрещённые зачины: " + ", ".join(data["openers"]))
    if data["phrases"]:
        lines.append("Запрещённые обороты: " + "; ".join(data["phrases"]))
    lines.append("Начни иначе и скажи это другими словами.")
    return "\n".join(lines)
