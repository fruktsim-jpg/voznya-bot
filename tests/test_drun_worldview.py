"""Тесты чистой логики мировоззрения друна (worldview).

Парсинг думы и помощники — детерминированы, тестируем без БД и LLM.
"""

from __future__ import annotations

from app.features.drun import worldview as wv


def test_parse_opinion():
    raw = '[{"type":"opinion","who":"Петя","text":"везучий шакал","weight":2}]'
    out = wv._parse_thoughts(raw)
    assert len(out) == 1
    assert out[0]["type"] == wv.KIND_OPINION
    assert out[0]["who"] == "Петя"
    assert out[0]["weight"] == 2


def test_parse_prediction_days_clamped():
    raw = '[{"type":"prediction","text":"сольёт всё","days":99,"weight":5}]'
    out = wv._parse_thoughts(raw)
    assert out[0]["type"] == wv.KIND_PREDICTION
    assert out[0]["days"] == 7        # clamp 1..7
    assert out[0]["weight"] == 3      # clamp 1..3


def test_parse_storyline():
    raw = 'мусор перед [{"type":"storyline","text":"война за топ"}] и после'
    out = wv._parse_thoughts(raw)
    assert len(out) == 1
    assert out[0]["type"] == wv.KIND_STORYLINE


def test_parse_rejects_unknown_type():
    raw = '[{"type":"nonsense","text":"x"},{"type":"opinion","who":"A","text":"b"}]'
    out = wv._parse_thoughts(raw)
    assert len(out) == 1
    assert out[0]["type"] == wv.KIND_OPINION


def test_parse_rejects_too_long():
    long_text = "x" * 300
    raw = f'[{{"type":"storyline","text":"{long_text}"}}]'
    assert wv._parse_thoughts(raw) == []


def test_parse_empty_array():
    assert wv._parse_thoughts("[]") == []
    assert wv._parse_thoughts("not json") == []


def test_has_material():
    empty = wv.WorldSnapshot(economy="", events=[], streaks=[], movers=[])
    assert not wv._has_material(empty)
    full = wv.WorldSnapshot(economy="", events=["x"], streaks=[], movers=[])
    assert wv._has_material(full)


def test_snapshot_text_includes_sections():
    snap = wv.WorldSnapshot(
        economy="ECO", events=["e1"], streaks=["s1"], movers=["m1"]
    )
    txt = wv._snapshot_text(snap)
    assert "ECO" in txt and "e1" in txt and "s1" in txt and "m1" in txt
