from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.features.drun import memory_recall as r


@dataclass
class Mem:
    id: int
    kind: str
    fact: str
    weight: int = 1
    subject_id: int | None = 10
    created_at: datetime | None = datetime(2026, 6, 1, tzinfo=timezone.utc)
    updated_at: datetime | None = None


def test_recall_caps_overrepresented_kind():
    memories = [
        Mem(i, "chat:nickname", f"юзера зовут кличка{i}", weight=3)
        for i in range(1, 8)
    ] + [
        Mem(20, "chat:meme", "постоянно шутит про чину", weight=2),
        Mem(21, "rivalry", "давняя дуэльная вражда с oew", weight=2),
    ]

    items = r.select_recall_items(memories, total_cap=12)
    nickname_items = [i for i in items if i.memory.kind == "chat:nickname"]

    assert len(nickname_items) == 2
    assert any(i.memory.kind == "chat:meme" for i in items)
    assert any(i.memory.kind == "rivalry" for i in items)


def test_recall_dedupes_same_fact_signature():
    items = r.select_recall_items([
        Mem(1, "trait", "Хинт любит pgvector", weight=1),
        Mem(2, "trait", "хинт любит pgvector!", weight=3),
    ])

    assert len(items) == 1
    # The first candidate wins after signature dedupe; DB cleanup is responsible
    # for permanent duplicate merging, recall only prevents prompt duplication.
    assert items[0].memory.id == 1


def test_recall_penalizes_recently_used_fact():
    repeated = Mem(1, "chat:trait", "любит сливать все деньги в казино", weight=3)
    fresh = Mem(2, "chat:trait", "часто спорит про музыку", weight=1)

    items = r.select_recall_items(
        [repeated, fresh],
        recent_posts=["опять ты любишь сливать все деньги в казино, легенда"],
    )

    assert items[0].memory.id == 2
    assert any(item.memory.id == 1 and item.repeated for item in items)


def test_recall_penalizes_recent_prompt_memory_id():
    used = Mem(1, "chat:trait", "любит сливать все деньги в казино", weight=3)
    fresh = Mem(2, "chat:trait", "часто спорит про музыку", weight=1)

    items = r.select_recall_items([used, fresh], recent_memory_ids=[1])

    assert items[0].memory.id == 2
    assert any(item.memory.id == 1 and item.repeated for item in items)


def test_render_recall_is_structured_and_warns_about_repeats():
    items = r.select_recall_items(
        [
            Mem(1, "chat:nickname", "h1nt в чате называют Хинт", weight=3),
            Mem(2, "episode:export", "однажды устроил спор на весь чат", weight=2),
        ],
        recent_posts=["h1nt в чате называют Хинт, хватит уже"],
    )

    rendered = r.render_recall(items)

    assert "# ДОЛГАЯ ПАМЯТЬ ДРУНА" in rendered
    assert "## Кто это / устойчивое досье" in rendered
    assert "## Конкретные эпизоды из истории" in rendered
    assert "## Уже заезжено недавно" in rendered
