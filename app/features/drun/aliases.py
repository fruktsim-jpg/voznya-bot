"""Псевдонимы/прозвища игроков: запоминание и резолв «как ещё зовут человека».

Друн должен понимать обращения вроде «забань артёма», даже если Артём — это
кличка, которую чат дал игроку с ником «Vasya777». Имя в Telegram (first_name/
@username) резолвит ``tools.find_user_id``; ЭТОТ модуль добавляет слой локальных
прозвищ, выученных из чата.

Хранилище — без новых таблиц: список в ``AiProfile.data["aliases"]`` вида
``[{"alias": "артём", "w": 2}, ...]``. Это переживает пересборку профиля
(profile.refresh_profile сохраняет накопленное) и подмешивается в досье.

Резолв терпим к русской морфологии (грубый стем) и к падежам: «артёма»,
«артему» → «артём». Любой сбой деградирует к None/пропуску — owner-команда
просто не найдёт игрока и честно об этом скажет.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.models import AiProfile, User

logger = get_logger(__name__)

# Сколько прозвищ максимум храним на игрока (анти-раздувание профиля).
_MAX_ALIASES = 12
# Минимальная длина значимого алиаса (отсекаем «он», «ты», шум).
_MIN_ALIAS_LEN = 3
# Стоп-набор: служебные слова, которые НЕ должны стать прозвищем.
_ALIAS_STOP = frozenset({
    "это", "вот", "тот", "там", "как", "что", "кто", "его", "him",
    "она", "они", "все", "наш", "мой", "the", "and", "for",
})


def _norm(text: str) -> str:
    """Нормализует алиас: lower, ё→е, без лишних пробелов и пунктуации по краям.

    ё→е намеренно: в чате одного человека пишут и «артём», и «артем» — для
    резолва это один и тот же алиас.
    """
    out = (text or "").strip().lower().lstrip("@").replace("ё", "е")
    return "".join(ch for ch in out if ch.isalnum() or ch in " -_").strip()


def _stem(word: str) -> str:
    """Грубый стем для падежей: режем типовые русские окончания (≤2 буквы)."""
    w = word
    if len(w) >= 5:
        for suf in ("ом", "ам", "ям", "ой", "ей", "ью", "ах", "ях", "ов", "ев"):
            if w.endswith(suf):
                return w[: -len(suf)]
    if len(w) >= 4:
        for suf in ("а", "у", "е", "ы", "и", "я", "ю", "о"):
            if w.endswith(suf):
                return w[: -len(suf)]
    return w


def _alias_matches(query: str, alias: str) -> bool:
    """Совпадает ли запрос с алиасом с учётом падежей (через грубый стем)."""
    q, a = _norm(query), _norm(alias)
    if not q or not a:
        return False
    if q == a:
        return True
    # Падежные формы: «артёма» vs «артём» — сравниваем стемы, но только если
    # они достаточно длинные (иначе «ко» совпадёт со всем).
    qs, as_ = _stem(q), _stem(a)
    return len(qs) >= _MIN_ALIAS_LEN and qs == as_


def add_aliases(prev: list[dict] | None, new_aliases: list[str]) -> list[dict]:
    """Сливает новые прозвища в список профиля, копя вес повторяемости.

    Возвращает обновлённый список ``[{"alias","w"}]`` (вес = сколько раз чат
    подтвердил это прозвище). Чем чаще зовут — тем выше приоритет при резолве.
    """
    by_alias: dict[str, int] = {}
    for item in prev or []:
        a = _norm(str(item.get("alias", "")))
        if a:
            by_alias[a] = max(by_alias.get(a, 0), int(item.get("w", 1) or 1))
    for raw in new_aliases:
        a = _norm(raw)
        if len(a) < _MIN_ALIAS_LEN or a in _ALIAS_STOP:
            continue
        by_alias[a] = by_alias.get(a, 0) + 1
    ranked = sorted(by_alias.items(), key=lambda kv: kv[1], reverse=True)
    return [{"alias": a, "w": w} for a, w in ranked[:_MAX_ALIASES]]


async def resolve_alias(session: AsyncSession, who: str) -> int | None:
    """Ищет игрока по выученному прозвищу. None — если совпадений нет.

    При коллизии (несколько профилей знают это прозвище) выбираем игрока с
    наибольшим суммарным весом алиаса (самое «устойчивое» прозвище). Поиск по
    профилям дешёвый: aliases лежат в JSONB и таблица невелика.
    """
    q = _norm(who)
    if len(q) < _MIN_ALIAS_LEN:
        return None
    try:
        rows = (
            await session.execute(
                select(AiProfile.user_id, AiProfile.data).where(
                    AiProfile.data.has_key("aliases")  # type: ignore[attr-defined]
                )
            )
        ).all()
    except Exception:  # noqa: BLE001
        logger.debug("resolve_alias query failed", exc_info=True)
        return None
    best_id: int | None = None
    best_w = 0
    for uid, data in rows:
        for item in (data or {}).get("aliases", []):
            alias = str(item.get("alias", ""))
            if _alias_matches(q, alias):
                w = int(item.get("w", 1) or 1)
                if w > best_w:
                    best_w, best_id = w, uid
    return best_id
