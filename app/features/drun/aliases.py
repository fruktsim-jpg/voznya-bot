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

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.models import AiProfile

logger = get_logger(__name__)

# Сколько прозвищ максимум храним на игрока (анти-раздувание профиля).
_MAX_ALIASES = 12
# Минимальная длина значимого алиаса (отсекаем «он», «ты», шум).
_MIN_ALIAS_LEN = 3
# TTL прозвища в днях по накопленному весу: слабые (разовый вброс/ошибка LLM)
# живут недолго и сами испаряются, устоявшиеся держатся месяцами. Это лечит
# alias-poisoning «навсегда»: мис-привязка с весом 1 (напр. чужое имя, по ошибке
# приклеенное к профилю) истекает за 2 недели, а реальная кличка, которую чат
# повторяет, копит вес и остаётся. Ключ — min(вес, 3).
_ALIAS_TTL_DAYS_BY_WEIGHT = {1: 14, 2: 30, 3: 90}
# Мин. накопленный вес прозвища, чтобы по нему РЕЗОЛВИТЬ owner-команду. Один-два
# вброса в чат (вес 1-2) не должны уводить бан/мут не на того — нужна
# устойчивость (ник реально прижился в чате).
_MIN_RESOLVE_WEIGHT = 3
# При коллизии (ник знают несколько игроков) лидер должен опережать второго
# хотя бы на столько, иначе считаем неоднозначным и не угадываем.
_RESOLVE_MARGIN = 2
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


def add_aliases(
    prev: list[dict] | None,
    new_aliases: list[str],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Сливает новые прозвища в список профиля, копя вес повторяемости.

    Возвращает обновлённый список ``[{"alias","w","ts"}]`` (вес = сколько раз чат
    подтвердил это прозвище, ``ts`` = когда подтверждали в последний раз). Чем
    чаще зовут — тем выше приоритет при резолве и тем дольше TTL. ``ts`` каждого
    подтверждённого сейчас прозвища освежается, чтобы реально живые клички не
    протухали (см. :func:`prune_expired`).
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    # alias → (вес, ts). Сохраняем прежний ts, если алиас не подтверждали сейчас.
    state: dict[str, tuple[int, str]] = {}
    for item in prev or []:
        a = _norm(str(item.get("alias", "")))
        if not a:
            continue
        w = int(item.get("w", 1) or 1)
        ts = str(item.get("ts") or now_iso)
        prev_w = state.get(a, (0, ts))[0]
        state[a] = (max(prev_w, w), ts)
    for raw in new_aliases:
        a = _norm(raw)
        if len(a) < _MIN_ALIAS_LEN or a in _ALIAS_STOP:
            continue
        w = state.get(a, (0, now_iso))[0] + 1
        state[a] = (w, now_iso)  # подтверждено сейчас → освежаем ts
    ranked = sorted(state.items(), key=lambda kv: kv[1][0], reverse=True)
    return [{"alias": a, "w": w, "ts": ts} for a, (w, ts) in ranked[:_MAX_ALIASES]]


def prune_expired(
    aliases: list[dict] | None, *, now: datetime | None = None
) -> list[dict]:
    """Выкидывает протухшие прозвища (TTL по весу, см. ``_ALIAS_TTL_DAYS_BY_WEIGHT``).

    Чистая функция: разовая мис-привязка (вес 1) истекает за 14 дней, вес 2 — за
    30, устоявшаяся кличка (вес ≥3) — за 90 дней без подтверждений. Алиас без
    ``ts`` (старый формат до этой правки) считаем «только что увиденным», чтобы
    не выкосить всю историю при первом проходе — он протухнет, только если его
    больше не подтверждают. Возвращает отфильтрованный список (порядок сохранён).
    """
    if not aliases:
        return []
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    for item in aliases:
        a = _norm(str(item.get("alias", "")))
        if not a:
            continue
        w = int(item.get("w", 1) or 1)
        ttl_days = _ALIAS_TTL_DAYS_BY_WEIGHT.get(max(1, min(3, w)), 14)
        ts_raw = item.get("ts")
        if not ts_raw:
            # Старый формат без ts — не роняем сразу, штампуем «сейчас».
            out.append({"alias": a, "w": w, "ts": now.isoformat()})
            continue
        try:
            last = datetime.fromisoformat(str(ts_raw))
            age_days = (now - last).total_seconds() / 86400.0
        except (ValueError, TypeError):
            age_days = 0.0
        if age_days <= ttl_days:
            out.append({"alias": a, "w": w, "ts": str(ts_raw)})
    return out


def drop_colliding_weak(
    aliases: list[dict] | None, foreign_names: set[str]
) -> list[dict]:
    """Выкидывает СЛАБЫЕ (вес ≤1) прозвища, совпадающие с именем ДРУГОГО игрока.

    Лечит классическую мис-привязку: LLM по ошибке приклеил к профилю А имя
    игрока Б (напр. «соня»/«маша» — это вообще другие люди). Сильные клички
    (вес ≥2, чат подтверждал не раз) не трогаем — совпадение с чьим-то именем
    может быть законным (тёзки). ``foreign_names`` — нормализованные имена ДРУГИХ
    игроков (без самого субъекта). Чистая функция.
    """
    if not aliases:
        return []
    out: list[dict] = []
    for item in aliases:
        a = _norm(str(item.get("alias", "")))
        w = int(item.get("w", 1) or 1)
        if w <= 1 and a in foreign_names:
            continue  # слабый алиас = чужое имя → почти наверняка мис-привязка
        out.append(item)
    return out


async def resolve_alias(session: AsyncSession, who: str) -> int | None:
    """Ищет игрока по выученному прозвищу. None — если совпадений нет/неоднозначно.

    Безопасность (alias-poisoning): прозвища вытаскивает LLM из ОБЫЧНОГО чата, и
    этот резолв питает owner-команды (бан/мут/выдача). Чтобы случайный/злонамеренно
    вброшенный ник не увёл команду не на того:

    * требуем УСТОЙЧИВОСТИ — алиас должен накопить вес ≥ ``_MIN_RESOLVE_WEIGHT``
      (одного-двух упоминаний мало);
    * при коллизии (ник знают несколько профилей) требуем явного ПЕРЕВЕСА
      лидера над вторым местом, иначе считаем неоднозначным и возвращаем None.

    Так owner-команда по сомнительной кличке честно не находит игрока, а не
    бьёт по «самому накрученному» кандидату.
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
    # Лучший вес матча на каждого игрока (несколько алиасов одного профиля могут
    # совпасть — берём сильнейший).
    weight_by_uid: dict[int, int] = {}
    for uid, data in rows:
        for item in (data or {}).get("aliases", []):
            alias = str(item.get("alias", ""))
            if _alias_matches(q, alias):
                w = int(item.get("w", 1) or 1)
                if w > weight_by_uid.get(uid, 0):
                    weight_by_uid[uid] = w
    if not weight_by_uid:
        return None
    return _pick_resolved(weight_by_uid)


def _pick_resolved(weight_by_uid: dict[int, int]) -> int | None:
    """Выбирает игрока из карты {uid: вес} с предохранителями anti-poisoning.

    Возвращает uid только если прозвище устоявшееся (вес ≥ _MIN_RESOLVE_WEIGHT)
    и лидер однозначно опережает второго (margin ≥ _RESOLVE_MARGIN). Иначе None.
    """
    if not weight_by_uid:
        return None
    ranked = sorted(weight_by_uid.items(), key=lambda kv: kv[1], reverse=True)
    best_id, best_w = ranked[0]
    if best_w < _MIN_RESOLVE_WEIGHT:
        return None  # прозвище ещё не устоявшееся — не рискуем
    # Коллизия: ник знают двое и веса близки → неоднозначно, не угадываем.
    if len(ranked) > 1 and best_w - ranked[1][1] < _RESOLVE_MARGIN:
        return None
    return best_id
