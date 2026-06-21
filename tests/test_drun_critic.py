from __future__ import annotations

from app.features.drun import critic


def test_critic_accepts_concrete_answer():
    result = critic.critique_response(
        query="помнишь когда хинт писал про pgvector",
        context="# СЫРОЙ АРХИВ ЧАТА\n- h1nt: pgvector норм",
        response="Да, Хинт тогда реально тащил pgvector как святыню и спорил за поиск.",
        archive_ids=[1],
    )

    assert result.ok is True
    assert result.reasons == ()


def test_critic_flags_generic_refusal():
    result = critic.critique_response(
        query="что было раньше",
        context="",
        response="Я не знаю, у меня нет информации.",
    )

    assert result.ok is False
    assert "generic_refusal" in result.reasons


def test_critic_flags_ungrounded_economy_claim():
    result = critic.critique_response(
        query="расскажи про хинта",
        context="# ДОЛГАЯ ПАМЯТЬ ДРУНА\n- спорит про музыку",
        response="У него баланс 999 ешек и он богатый.",
    )

    assert result.ok is False
    assert "ungrounded_economy_claim" in result.reasons


def test_critic_allows_grounded_economy_claim():
    result = critic.critique_response(
        query="у кого больше ешек",
        context="# ЭКОНОМИКА\n- h1nt: 10 ешек",
        response="По экономике Хинт не выглядит главным богачом.",
    )

    assert "ungrounded_economy_claim" not in result.reasons


def test_repair_trims_too_long_response():
    long = "x" * 2000
    result = critic.critique_response(query="x", context="", response=long)

    repaired = critic.repair_response_text(long, result)

    assert len(repaired) < len(long)
    assert repaired.endswith("…")
