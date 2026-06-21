"""Тесты псевдонимов/прозвищ друна (чистая логика, без БД).

Сторожит ключевой кейс: «забань артёма», где Артём — выученная кличка. Проверяем
нормализацию, падежный матч (через стем), накопление веса и дедуп.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.features.drun import aliases as al


def test_norm_strips_punct_and_at():
    # ё→е намеренно нормализуется (в чате пишут и так, и так).
    assert al._norm("  @Артём! ") == "артем"
    assert al._norm("Vasya_777") == "vasya_777"


def test_alias_matches_cases():
    # Точное и падежные формы одного прозвища (ё/е считаются одним).
    assert al._alias_matches("артём", "артём")
    assert al._alias_matches("артёма", "артём")
    assert al._alias_matches("артему", "артём")
    # Разные имена не совпадают.
    assert not al._alias_matches("артём", "максим")


def test_add_aliases_accumulates_weight_and_dedups():
    prev = [{"alias": "артем", "w": 1}]
    out = al.add_aliases(prev, ["Артём", "артём", "Тёма"])
    by = {x["alias"]: x["w"] for x in out}
    assert by["артем"] >= 3  # 1 prev + 2 confirmations (ё→е сливает формы)
    assert "тема" in by


def test_add_aliases_drops_short_and_stopwords():
    out = al.add_aliases(None, ["он", "это", "ок", "Артём"])
    aliases = {x["alias"] for x in out}
    assert "артем" in aliases
    assert "он" not in aliases and "это" not in aliases and "ок" not in aliases


def test_add_aliases_caps_count():
    many = [f"клич{i}ка" for i in range(30)]
    out = al.add_aliases(None, many)
    assert len(out) <= al._MAX_ALIASES


def test_pick_resolved_requires_min_weight():
    # Свежий вброс (вес 1-2) НЕ должен резолвиться в owner-команду.
    assert al._pick_resolved({100: 1}) is None
    assert al._pick_resolved({100: 2}) is None
    # Устоявшееся прозвище — резолвится.
    assert al._pick_resolved({100: 3}) == 100


def test_pick_resolved_ambiguous_collision_returns_none():
    # Двое знают ник с близким весом → неоднозначно, не угадываем.
    assert al._pick_resolved({100: 4, 200: 3}) is None
    # Явный перевес лидера → резолвится в него.
    assert al._pick_resolved({100: 6, 200: 3}) == 100


def test_pick_resolved_empty():
    assert al._pick_resolved({}) is None


def test_add_aliases_stamps_ts_and_refreshes_on_confirm():
    # Каждый алиас получает ts; подтверждение сейчас освежает ts старого.
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    prev = [{"alias": "артем", "w": 1, "ts": old}]
    now = datetime.now(timezone.utc)
    out = al.add_aliases(prev, ["артём"], now=now)
    rec = next(x for x in out if x["alias"] == "артем")
    assert rec["w"] == 2
    assert rec["ts"] == now.isoformat()  # подтверждён сейчас → ts освежён


def test_add_aliases_keeps_old_ts_when_not_confirmed():
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    prev = [{"alias": "тёма", "w": 2, "ts": old}]
    out = al.add_aliases(prev, ["максим"], now=datetime.now(timezone.utc))
    kept = next(x for x in out if x["alias"] == "тема")
    assert kept["ts"] == old  # не трогали — ts прежний


def test_prune_expired_drops_stale_weak_alias():
    now = datetime.now(timezone.utc)
    aliases = [
        # вес 1, TTL 14д, последний раз 20 дней назад → выкидываем (мис-привязка).
        {"alias": "эдик", "w": 1, "ts": (now - timedelta(days=20)).isoformat()},
        # вес 3, TTL 90д, 20 дней назад → остаётся (устоявшаяся кличка).
        {"alias": "таня", "w": 3, "ts": (now - timedelta(days=20)).isoformat()},
    ]
    out = al.prune_expired(aliases, now=now)
    names = {x["alias"] for x in out}
    assert "эдик" not in names
    assert "таня" in names


def test_prune_expired_stamps_legacy_alias_without_ts():
    # Старый формат без ts не выкидываем сразу — штампуем «сейчас».
    now = datetime.now(timezone.utc)
    out = al.prune_expired([{"alias": "босс", "w": 5}], now=now)
    assert len(out) == 1
    assert out[0]["ts"] == now.isoformat()


def test_prune_expired_empty():
    assert al.prune_expired(None) == []
    assert al.prune_expired([]) == []


def test_drop_colliding_weak_removes_foreign_name():
    # «соня» (вес 1) = имя другого игрока → выкидываем как мис-привязку.
    aliases = [
        {"alias": "соня", "w": 1, "ts": None},
        {"alias": "босс", "w": 5, "ts": None},
    ]
    out = al.drop_colliding_weak(aliases, {"соня", "маша"})
    names = {x["alias"] for x in out}
    assert "соня" not in names
    assert "босс" in names  # не чужое имя — остаётся


def test_drop_colliding_weak_keeps_strong_namesake():
    # Сильная кличка (вес ≥2), совпавшая с чьим-то именем — законный тёзка, не трогаем.
    aliases = [{"alias": "макс", "w": 3, "ts": None}]
    out = al.drop_colliding_weak(aliases, {"макс"})
    assert len(out) == 1


def test_drop_colliding_weak_empty():
    assert al.drop_colliding_weak(None, {"соня"}) == []
    assert al.drop_colliding_weak([], set()) == []
