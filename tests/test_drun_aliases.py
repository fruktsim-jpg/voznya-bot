"""Тесты псевдонимов/прозвищ друна (чистая логика, без БД).

Сторожит ключевой кейс: «забань артёма», где Артём — выученная кличка. Проверяем
нормализацию, падежный матч (через стем), накопление веса и дедуп.
"""

from __future__ import annotations

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
