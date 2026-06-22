from __future__ import annotations

from app.features.drun import answer_planner as p


def test_joke_plan_blocks_economy_duel_fallback():
    plan = p.build_answer_plan(query="расскажи анекдот", response_mode="joke", context_intent="default", context="")
    text = plan.render()
    assert "новую шутку" in text
    assert "ешки/казино/дуэли" in text
    assert "сетап + панчлайн" in text


def test_help_plan_requires_steps():
    plan = p.build_answer_plan(query="как заработать ешки", response_mode="help", context_intent="economy", context="# ЭКОНОМИКА")
    text = plan.render()
    assert "гайд" in text
    assert "2-5 шагов" in text


def test_person_plan_uses_dossier_and_caution():
    ctx = "# КТО ЭТО МОЖЕТ БЫТЬ\n# АВТО-ДОСЬЕ ЧЕЛОВЕКА\n# ДОЛГАЯ ПАМЯТЬ ДРУНА"
    plan = p.build_answer_plan(query="кто такая Карина", response_mode="question", context_intent="person", context=ctx)
    text = plan.render()
    assert "identity/dossier" in text
    assert "confidence низкий" in text


def test_vent_plan_avoids_rofl():
    plan = p.build_answer_plan(query="мне плохо", response_mode="vent", context_intent="default", context="")
    text = plan.render()
    assert "поддержать" in text
    assert "рофл по боли" in text


def test_economy_plan_requires_context_data():
    plan = p.build_answer_plan(query="у кого больше ешек", response_mode="question", context_intent="economy", context="# ЭКОНОМИКА")
    text = plan.render()
    assert "строго по данным" in text
    assert "придумывать суммы" in text
