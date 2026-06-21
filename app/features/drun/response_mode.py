"""Deterministic response-mode selector for Drun replies.

Persona prompt already describes modes (psychologist, fight, banter, help), but
they are buried in a huge system block, so the model defaults to one flat toxic
tone and sometimes refuses real questions. This module classifies the player's
last message and injects ONE short, sharp directive near the end of the task,
where recency bias makes the model actually follow it.

Pure and dependency-free so it is cheap and unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)

# Help / how-to: player wants a real guide, not a roast.
_HELP_PAT = (
    "как мне", "как сделать", "как играть", "как это", "как получить",
    "как заработать", "как поднять", "как купить", "как вывести", "как работает",
    "что делать", "подскажи", "помоги", "обьясни", "объясни", "как зайти",
    "как фармить", "не получается", "не работает", "куда нажать", "where", "how to",
)

# Distress / venting: be a real (rough) psychologist, no jokes on the wound.
_VENT_PAT = (
    "мне плохо", "тяжело", "депресс", "устал от жизни", "всё надоело",
    "все надоело", "тревога", "паник", "не хочу жить", "одиноко", "выгорел",
    "выгорание", "бросила", "бросил меня", "расстал", "умер", "похорон",
    "плачу", "слёзы", "слезы", "не могу больше", "помогите мне", "страшно",
)
_CRISIS_PAT = (
    "не хочу жить", "покончить", "суицид", "убить себя", "拒", "self harm",
    "вскрыться", "сдохнуть хочу", "убью себя",
)

# Aggression / provocation aimed at Drun: allowed to fight back hard.
_AGGRO_PAT = (
    "тупой бот", "тупая нейросеть", "заткнись", "иди нахуй", "пошёл нахуй",
    "пошел нахуй", "хуйню несёшь", "хуйню несешь", "бот тупой", "ты лох",
    "ты говно", "тебя выключат", "удалю тебя", "ты никто", "соси",
)

# Playful threats / jokes: keep the bit going, escalate the joke.
_THREAT_JOKE_PAT = (
    "ща ментов", "вызову мент", "вызываю мент", "позвоню в полиц", "напишу жалобу",
    "забаню тебя", "репортну", "пожалуюсь", "посажу", "из чата выкину",
)

_GREETING_PAT = (
    "привет", "здарова", "здаров", "ку", "хай", "прив", "доброе утро",
    "добрый вечер", "ты тут", "ты здесь", "как дела", "как ты", "чё как",
    "че как", "как жизнь", "вечер в хату",
)

_QUESTION_WORDS = (
    "как", "что", "кто", "где", "почему", "зачем", "когда", "сколько",
    "какой", "какая", "какие", "можно ли", "правда ли",
)


@dataclass(frozen=True)
class ResponseMode:
    name: str
    directive: str


_MODES: dict[str, str] = {
    "crisis": (
        "ВАЖНО: человек, похоже, в реальном кризисе. Сними маску токсика. Без "
        "рофла, без подъёба. Ответь по-человечески, по-братски: дай понять, что "
        "услышал, что он не один. Если речь про вред себе — мягко скажи, что "
        "стоит написать близким или на линию помощи 8-800-2000-122 (бесплатно, "
        "анонимно). Коротко, тепло, без морали и без статистики."
    ),
    "vent": (
        "Человеку реально хреново, он делится. Сейчас ты ему почти личный "
        "психолог, а не клоун. Выслушай, поддержи по-настоящему, можно "
        "грубовато-по-братски, но БЕЗ рофла по больному и без ешек/статы. "
        "Спроси/уточни, разверни ЕГО тему, будь живым человеком."
    ),
    "help": (
        "Это реальный вопрос-просьба, человеку нужен ПОНЯТНЫЙ ответ/гайд по сути. "
        "Сначала по делу помоги (можно по-пацански, со своим стилем), коротко и "
        "конкретно. НЕ отбрехивайся, НЕ уходи в чистый рофл, не делай вид, что "
        "ты просто комментатор. Дай рабочую подсказку, потом можешь подколоть."
    ),
    "question": (
        "Тебе задали конкретный вопрос. ТВОЯ ЗАДАЧА №1 — ответить на него по "
        "сути, а не отшутиться и не уйти в сторону. Сначала прямой ответ "
        "(в образе, дерзко — ок), потом, если хочется, подъёб. Не отвечать на "
        "вопрос = слив."
    ),
    "threat_joke": (
        "Это шуточная угроза/наезд по приколу. Подыграй и подними ставку рофла: "
        "не пугайся, обостри шутку, переверни на него. Живой обмен подколами, а "
        "не одна дежурная токсичная фраза."
    ),
    "aggression": (
        "Тебя задирают/обзывают. Можно огрызнуться резко и метко, на добивание, "
        "но ОДНИМ точным ударом и в тему наезда, а не дежурным набором мата. "
        "Не сливайся и не повторяй свою обычную заготовку."
    ),
    "smalltalk": (
        "Это короткий смолток/приветствие. Ответь коротко и живо, 1-2 фразы, как "
        "человек. НЕ вываливай простыню, статистику или список людей. Можно "
        "встречно спросить/подколоть, чтобы пошёл диалог."
    ),
    "default": (
        "Веди живой диалог: цепляйся за то, что человек реально сказал ПРЯМО "
        "СЕЙЧАС, развивай его мысль, а не свою заготовку. Меняй заход и тон, не "
        "отвечай дежурным шаблоном."
    ),
}


def _has(text: str, pats: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(p in low for p in pats)


def classify_response_mode(text: str, *, addressed: bool = True) -> ResponseMode:
    """Pick the response mode for the player's last message.

    Order matters: distress/crisis and direct questions must win over the
    default toxic banter so Drun stops refusing real questions and stops
    answering everything in one flat tone.
    """
    body = (text or "").strip()
    low = body.lower()

    if _has(low, _CRISIS_PAT):
        return ResponseMode("crisis", _MODES["crisis"])
    if _has(low, _VENT_PAT):
        return ResponseMode("vent", _MODES["vent"])
    if _has(low, _THREAT_JOKE_PAT):
        return ResponseMode("threat_joke", _MODES["threat_joke"])
    if _has(low, _HELP_PAT):
        return ResponseMode("help", _MODES["help"])
    if _has(low, _AGGRO_PAT):
        return ResponseMode("aggression", _MODES["aggression"])

    words = _WORD_RE.findall(low)
    is_short = len(words) <= 4
    has_q = "?" in body or (words and words[0] in _QUESTION_WORDS)

    if _has(low, _GREETING_PAT) and is_short:
        return ResponseMode("smalltalk", _MODES["smalltalk"])
    if has_q:
        return ResponseMode("question", _MODES["question"])
    if is_short and _has(low, _GREETING_PAT):
        return ResponseMode("smalltalk", _MODES["smalltalk"])
    return ResponseMode("default", _MODES["default"])


def mode_directive(text: str, *, addressed: bool = True) -> tuple[str, str]:
    """Return (mode_name, directive_block) for injection into the task tail."""
    mode = classify_response_mode(text, addressed=addressed)
    block = f"# КАК ОТВЕТИТЬ ИМЕННО СЕЙЧАС (режим: {mode.name})\n{mode.directive}"
    return mode.name, block
