from __future__ import annotations

from app.features.drun import identity


def test_normalize_name_lowers_and_strips_punctuation():
    assert identity.normalize_name(" Хинт!!! ") == "хинт"
    assert identity.normalize_name("h1nt_jpg") == "h1nt_jpg"


def test_extract_person_query_removes_common_prefixes():
    assert identity.extract_person_query("кто такой Хинт вообще") == "Хинт"
    assert identity.extract_person_query("досье на oew") == "oew"
    assert identity.extract_person_query("человек найти фрукта") == "фрукта"


def test_rank_candidates_adds_evidence_bonus_and_sorts():
    weak_with_evidence = identity.PersonCandidate(
        user_id=1,
        name="Хинт",
        confidence=0.60,
        sources=["archive_name_fuzzy"],
        archive_hits=200,
    )
    strong_no_evidence = identity.PersonCandidate(
        user_id=2,
        name="Хинтик",
        confidence=0.70,
        sources=["memory_fact"],
    )

    ranked = identity.rank_candidates([strong_no_evidence, weak_with_evidence])

    assert ranked[0].user_id == 1
    assert ranked[0].confidence > 0.70


def test_render_candidates_marks_low_confidence():
    rendered = identity.render_candidates([
        identity.PersonCandidate(
            user_id=10,
            name="maybe",
            confidence=0.4,
            sources=["memory_fact"],
        )
    ])

    assert "низкая уверенность" in rendered
    assert "user_id=10" in rendered


def test_render_candidates_empty_warns_not_to_invent():
    rendered = identity.render_candidates([])

    assert "кандидатов не найдено" in rendered
    assert "не выдумывай" in rendered


def test_render_dossier_includes_caution_facts_and_archive_lines():
    dossier = identity.PersonDossier(
        candidate=identity.PersonCandidate(
            user_id=10,
            name="Хинт",
            confidence=0.6,
            aliases=["Хинт", "h1nt"],
        ),
        memories=[identity.DossierMemoryLine(kind="chat:trait", fact="любит pgvector", weight=3)],
        relationships=[
            identity.DossierRelationshipLine(
                user_id=20,
                name="oew",
                count=7,
                direction="он отвечал им",
            )
        ],
        archive_lines=[identity.DossierArchiveLine(name="h1nt", text="pgvector норм", message_at=None)],
    )

    rendered = identity.render_dossier(dossier)

    assert "# АВТО-ДОСЬЕ ЧЕЛОВЕКА" in rendered
    assert "Низкая уверенность" in rendered
    assert "любит pgvector" in rendered
    assert "reply-граф" in rendered
    assert "oew" in rendered
    assert "pgvector норм" in rendered
