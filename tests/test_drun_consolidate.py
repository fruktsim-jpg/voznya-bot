"""Тесты консолидации памяти: свежие факты вытесняют устаревшие по теме."""

from __future__ import annotations

from app.features.drun import consolidate as c


def test_detect_topic_basic():
    assert c.detect_topic("живёт в Москве") == "location"
    assert c.detect_topic("работает программистом") == "job"
    assert c.detect_topic("ему 25 лет") == "age"
    assert c.detect_topic("просто любит рофлить") is None


def test_new_location_supersedes_old():
    prev = ["живёт в Москве", "любит кейсы"]
    new = ["переехал в Питер"]
    out = c.merge_self_facts(prev, new)
    assert "переехал в Питер" in out
    assert "живёт в Москве" not in out  # вытеснено по теме location
    assert "любит кейсы" in out          # не тема — осталось


def test_exact_duplicates_collapse():
    out = c.merge_self_facts(["любит кейсы"], ["любит кейсы", "любит кейсы"])
    assert out.count("любит кейсы") == 1


def test_untopiced_facts_accumulate():
    prev = ["любит рофлить", "фанат дотки"]
    new = ["коллекционирует ножи"]
    out = c.merge_self_facts(prev, new)
    assert set(out) == {"любит рофлить", "фанат дотки", "коллекционирует ножи"}


def test_cap_keeps_newest():
    prev = [f"факт{i} разное" for i in range(20)]
    out = c.merge_self_facts(prev, ["свежий факт"], cap=5)
    assert len(out) == 5
    assert "свежий факт" in out  # самый свежий сохранён


def test_job_change_supersedes():
    out = c.merge_self_facts(
        ["работает курьером"], ["устроился в офис менеджером"]
    )
    assert "работает курьером" not in out
    assert "устроился в офис менеджером" in out
