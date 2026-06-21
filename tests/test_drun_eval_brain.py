from __future__ import annotations

from scripts import drun_eval_brain as e
from app.features.drun import identity


def test_candidate_dict_is_compact():
    cand = identity.PersonCandidate(
        user_id=1,
        name="Карина",
        confidence=0.7777,
        sources=["a", "b"],
        aliases=["Карина"],
        archive_hits=5,
        memory_hits=2,
    )

    out = e._candidate_dict(cand)

    assert out["confidence"] == 0.778
    assert out["user_id"] == 1
    assert out["archive_hits"] == 5


def test_default_cases_cover_key_modes():
    joined = "\n".join(e.DEFAULT_CASES).lower()

    assert "карин" in joined
    assert "ешк" in joined
    assert "мне плохо" in joined
    assert "ментов" in joined
