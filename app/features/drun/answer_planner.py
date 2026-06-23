"""Lightweight answer planner for Drun.

Response modes decide tone/interaction type. The planner decides what the answer
must accomplish: goal, evidence to use, what to avoid, and a flexible format. It
is deterministic and prompt-only: no tools, no economy writes, no hidden actions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnswerPlan:
    goal: str
    use: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    format: str = "1-4 живые фразы, без простыни"

    def render(self) -> str:
        lines = ["# ПЛАН ОТВЕТА (не шаблон, а цель)", f"Цель: {self.goal}"]
        if self.use:
            lines.append("Используй:")
            lines.extend(f"- {item}" for item in self.use)
        if self.avoid:
            lines.append("Избегай:")
            lines.extend(f"- {item}" for item in self.avoid)
        lines.append(f"Формат: {self.format}")
        lines.append(
            "Важно: план не должен убивать живость. Можно импровизировать, "
            "но нельзя игнорировать цель ответа."
        )
        return "\n".join(lines)


def _has_block(context: str, marker: str) -> bool:
    return marker in (context or "")


def build_answer_plan(
    *,
    query: str | None,
    response_mode: str,
    context_intent: str,
    context: str | None = None,
) -> AnswerPlan:
    """Build final-task planning guidance from mode + routed context."""
    ctx = context or ""
    has_identity = _has_block(ctx, "# КТО ЭТО МОЖЕТ БЫТЬ") or _has_block(ctx, "# АВТО-ДОСЬЕ")
    has_archive = _has_block(ctx, "# СЫРОЙ АРХИВ") or _has_block(ctx, "Реальные упоминания")
    has_memory = _has_block(ctx, "# ДОЛГАЯ ПАМЯТЬ")
    has_economy = "ЭКОНОМИ" in ctx

    if response_mode == "joke":
        use = ["один свежий локальный образ из контекста/лора/чата"]
        if has_identity:
            use.append("person-досье/клички аккуратно, без уверенного бреда")
        return AnswerPlan(
            goal="сочинить новую шутку/анекдот, а не дежурный токсичный панч",
            use=tuple(use),
            avoid=(
                "ешки/казино/дуэли/КД, если их не просили явно",
                "повтор старого подкола из памяти",
                "объяснение шутки вместо самой шутки",
            ),
            format="сетап + панчлайн; 1-3 короткие фразы",
        )

    if response_mode == "fun_fact":
        use = ["один конкретный факт/эпизод из памяти, архива или летописи"]
        if has_archive:
            use.append("сырую архивную реплику как фактуру, не как цитатник")
        if has_memory:
            use.append("долгую память как проверенную зацепку")
        return AnswerPlan(
            goal="дать забавный факт или короткую байку из мира/чата, а не дежурный роаст",
            use=tuple(use),
            avoid=(
                "ешки/казино/дуэли как fallback, если их не просили",
                "общую фразу без факта",
                "выдумывать точные цитаты, которых нет в архиве",
            ),
            format="1 конкретный факт/сцена + 1 панч/отношение Друна",
        )

    if response_mode == "help":
        return AnswerPlan(
            goal="дать полезный ответ или гайд, который реально можно применить",
            use=("контекст и правила мира, если они есть", "если данных мало — честно назвать пробел"),
            avoid=("чистый рофл вместо инструкции", "уход в личные подколы", "выдуманные факты"),
            format="2-5 шагов или короткий список; в конце можно один подкол",
        )

    if response_mode in {"vent", "crisis"}:
        return AnswerPlan(
            goal="поддержать человека и продолжить живой разговор, а не победить в перепалке",
            use=("эмпатию", "один простой следующий шаг", "мягкий встречный вопрос"),
            avoid=("рофл по боли", "ешки/дуэли/стату", "обесценивание"),
            format="2-4 тёплые фразы; без лекции",
        )

    if context_intent == "person":
        use = ["identity/dossier как главный источник"]
        if has_archive:
            use.append("1 конкретное упоминание/реплику из архива")
        if has_memory:
            use.append("1-2 сильных факта из памяти, не всё подряд")
        return AnswerPlan(
            goal="ответить, кто это/что известно о человеке, с уровнем уверенности",
            use=tuple(use),
            avoid=("если confidence низкий — не говорить уверенно", "ешки/дуэли без запроса", "смешивать разных людей"),
            format="короткое досье: кто вероятно, чем известен, 1 факт/эпизод, оговорка при низкой уверенности",
        )

    if context_intent == "past":
        return AnswerPlan(
            goal="вспомнить прошлое через evidence, а не фантазировать",
            use=("archive hits/упоминания", "memory facts только как дополнение"),
            avoid=("общие рассуждения вместо конкретики", "экономику, если не спрашивали"),
            format="1-3 конкретных факта/эпизода + вывод в стиле Друна",
        )

    if context_intent == "economy" or has_economy:
        return AnswerPlan(
            goal="ответить про экономику строго по данным контекста",
            use=("economy block", "балансы/статы только если они есть в контексте"),
            avoid=("придумывать суммы", "переносить экономику в любой другой разговор"),
            format="короткий вывод + 1 деталь из данных",
        )

    if response_mode == "question":
        return AnswerPlan(
            goal="дать прямой ответ на вопрос, затем уже стиль/подкол",
            use=("самую релевантную часть контекста",),
            avoid=("уход в сторону", "дежурный токсичный шаблон", "ответ вопросом на вопрос без пользы"),
            format="прямой ответ первым предложением; дальше 1-2 фразы развития",
        )

    return AnswerPlan(
        goal="продолжить живой диалог по последней реплике человека",
        use=("последнюю реплику как главный якорь", "память только если она реально к месту"),
        avoid=("один и тот же заход", "случайные ешки/дуэли", "простыню"),
        format="1-4 фразы; можно закончить зацепкой для ответа",
    )
