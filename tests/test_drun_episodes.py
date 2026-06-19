"""Тесты эпизодов отношений (episodes) и прямых сдвигов мнения — LEAP-5.

Чистые ядра без БД: таксономия типов, дельты по значимости, рендер досье,
прямой (не-EMA) сдвиг вектора мнения памятным моментом, парсинг эпизодов.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.features.drun import episodes, opinions
from app.features.drun.episodes import Episode
from app.features.drun.opinions import AXES


# --- таксономия --------------------------------------------------------------


def test_all_types_have_valid_deltas():
    for code in episodes.ALL_TYPES:
        et = episodes.episode_type(code)
        assert et is not None
        assert et.base_significance in (1, 2, 3)
        assert et.valence in (-1, 0, 1)
        # дельты двигают только реальные оси мнения
        for ax in et.deltas:
            assert ax in AXES


def test_betrayal_destroys_trust():
    d = episodes.deltas_for("betrayal", significance=3)
    assert d["trust"] < 0
    assert abs(d["trust"]) >= 20  # яркое предательство бьёт сильно


def test_support_and_defense_raise_trust():
    assert episodes.deltas_for("support")["trust"] > 0
    assert episodes.deltas_for("defense")["trust"] > 0


def test_kept_vs_broken_promise_opposite_reliability():
    assert episodes.deltas_for("kept_promise")["reliability"] > 0
    assert episodes.deltas_for("broken_promise")["reliability"] < 0


def test_significance_scales_magnitude():
    low = episodes.deltas_for("humiliation", significance=1)
    high = episodes.deltas_for("humiliation", significance=3)
    assert abs(high["respect"]) > abs(low["respect"])


def test_unknown_type_yields_no_deltas():
    assert episodes.deltas_for("nonsense") == {}
    assert episodes.episode_type("nonsense") is None


def test_kind_roundtrip():
    k = episodes.kind_for("betrayal")
    assert k == "episode:betrayal"
    assert episodes.code_from_kind(k) == "betrayal"


def test_ttl_grows_with_significance():
    assert episodes._TTL_DAYS_BY_SIG[1] < episodes._TTL_DAYS_BY_SIG[2]
    assert episodes._TTL_DAYS_BY_SIG[2] < episodes._TTL_DAYS_BY_SIG[3]


# --- рендер досье ------------------------------------------------------------


def test_render_block_empty():
    assert episodes.render_block([]) == ""


def test_render_block_marks_valence_and_age():
    eps = [
        Episode("betrayal", "предательство", "кинул на дуэли", 3, -1, 0.5),
        Episode("support", "поддержка", "заступился в споре", 2, 1, 10.0),
    ]
    out = episodes.render_block(eps)
    assert "ЧТО ОН ДЕЛАЛ" in out
    assert "предательство" in out and "поддержка" in out
    assert "сегодня" in out          # age < 1 день
    assert "10д назад" in out
    assert "－" in out and "＋" in out  # маркеры валентности


# --- прямой сдвиг мнения (opinions.apply_deltas) -----------------------------


def test_apply_deltas_moves_immediately_unlike_ema():
    # Памятный момент двигает СРАЗУ и заметно, в отличие от медленного evolve.
    base = {"axes": {ax: 50.0 for ax in AXES}, "samples": 10,
            "ts": datetime.now(timezone.utc).isoformat()}
    after = opinions.apply_deltas(base, episodes.deltas_for("betrayal", significance=3))
    assert after["axes"]["trust"] < 35.0  # обвал доверия за один эпизод
    assert after["samples"] == 11


def test_apply_deltas_clamps():
    base = {"axes": {ax: 95.0 for ax in AXES}, "samples": 5,
            "ts": datetime.now(timezone.utc).isoformat()}
    after = opinions.apply_deltas(base, {"trust": +50.0})
    assert after["axes"]["trust"] == 100.0


def test_apply_deltas_decays_stale_first():
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    base = {"axes": {ax: 100.0 for ax in AXES}, "samples": 30, "ts": old}
    after = opinions.apply_deltas(base, {"respect": +5.0})
    # 120 дней простоя стянули respect к нейтралу сильнее, чем +5 подняли.
    assert after["axes"]["respect"] < 100.0


def test_apply_deltas_on_empty_starts_from_neutral():
    after = opinions.apply_deltas(None, episodes.deltas_for("humiliation", significance=2))
    assert after["axes"]["respect"] < 50.0
    assert after["axes"]["annoyance"] > 50.0
    assert after["samples"] == 1


def test_episode_meaningfully_outweighs_one_aggregate_step():
    # История отношений важнее сырой статы: один памятный эпизод сдвигает доверие
    # сильнее, чем один шаг агрегатного наблюдения с противоположным знаком.
    start = {"axes": {ax: 50.0 for ax in AXES}, "samples": 10,
             "ts": datetime.now(timezone.utc).isoformat()}
    via_episode = opinions.apply_deltas(start, episodes.deltas_for("betrayal", significance=3))
    via_aggregate = opinions.merge_observation(start, {"trust": 100.0})
    drop = 50.0 - via_episode["axes"]["trust"]
    rise = via_aggregate["axes"]["trust"] - 50.0
    assert drop > rise  # эпизод движет заметно сильнее одного шага статы


# --- парсинг эпизодов из ответа LLM (chat_memory._parse_episodes) ------------


def test_parse_episodes_valid():
    from app.features.drun import chat_memory

    raw = (
        '[{"name":"Vasya","type":"betrayal","gist":"кинул напарника на дуэли",'
        '"significance":3},'
        '{"name":"Petya","type":"support","gist":"заступился за новичка",'
        '"significance":2}]'
    )
    out = chat_memory._parse_episodes(raw)
    assert len(out) == 2
    assert out[0]["type"] == "betrayal" and out[0]["significance"] == 3
    assert out[1]["name"] == "Petya"


def test_parse_episodes_drops_unknown_type_and_empty():
    from app.features.drun import chat_memory

    raw = (
        '[{"name":"X","type":"nonsense","gist":"что-то"},'
        '{"name":"","type":"support","gist":"пусто имя"},'
        '{"name":"Y","type":"defense","gist":"прикрыл"}]'
    )
    out = chat_memory._parse_episodes(raw)
    assert len(out) == 1
    assert out[0]["type"] == "defense"


def test_parse_episodes_garbage_returns_empty():
    from app.features.drun import chat_memory

    assert chat_memory._parse_episodes("не json вообще") == []
    assert chat_memory._parse_episodes("") == []


def test_parse_episodes_clamps_significance():
    from app.features.drun import chat_memory

    raw = '[{"name":"X","type":"whining","gist":"ноет","significance":9}]'
    out = chat_memory._parse_episodes(raw)
    assert out[0]["significance"] == 3

