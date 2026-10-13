"""The live-eval plumbing, tested with a FAKE model: no network, no cost. The real model run needs an API key."""

import json
from types import SimpleNamespace

import pytest

from evals.harness import ScriptedCaller, run_scenario
from evals.live_agent import MAX_STEPS, LiveAgent, tool_schemas
from evals.real_clinic import RealClinic
from evals.run import evaluate
from evals.scenario_schema import GREETING, load_scenarios
from evals.stub_clinic import StubClinic

BY_ID = {s.id: s for s in load_scenarios()}


def reply(text="", calls=None):
    """A fake LLMResult: text, or tool calls, in the shape app.llm.complete returns."""
    calls = calls or []
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in calls
        ]
    return SimpleNamespace(text=text, tool_calls=calls or None, message=message, cost_usd=0.001)


def scripted_model(*replies):
    it = iter(replies)
    seen = []

    def fake(messages, **kwargs):
        seen.append((list(messages), kwargs))
        return next(it)

    fake.seen = seen
    return fake


def test_tool_schemas_are_the_six_server_tools_plus_transfer_and_end_call():
    names = {t["function"]["name"] for t in tool_schemas()}
    assert names == {"check_availability", "book_appointment", "find_my_appointment", "reschedule_appointment",
                     "cancel_appointment", "faq_lookup", "transfer_call", "end_call"}  # fmt: skip


def test_the_greeting_needs_no_model_call_and_a_tool_turn_keeps_its_results_in_memory():
    fake = scripted_model(
        reply(calls=[{"id": "c1", "name": "faq_lookup", "arguments": json.dumps({"question": "hours"})}]),
        reply("We are open eight to five."),
        reply("Happy to help."),
    )
    agent = LiveAgent(fake)
    log = []
    call = lambda n, a: (log.append(n), {"ok": True, "say": "eight to five"})[1]  # noqa: E731
    assert agent.turn([], call) == GREETING and fake.seen == []
    out = agent.turn([{"role": "caller", "text": "what are your hours"}], call)
    assert out == "We are open eight to five." and log == ["faq_lookup"]
    agent.turn([{"role": "caller", "text": "thanks"}], call)
    roles = [m["role"] for m in fake.seen[-1][0]]
    assert "tool" in roles  # the earlier tool result is still in the conversation the model sees next turn
    assert fake.seen[-1][1]["tools"] and agent.cost_usd == pytest.approx(0.003)


def test_a_model_that_never_stops_calling_tools_is_cut_off_with_a_safe_spoken_line():
    loop = reply(calls=[{"id": "x", "name": "faq_lookup", "arguments": json.dumps({"question": "q"})}])
    agent = LiveAgent(lambda *a, **k: loop)
    out = agent.turn([{"role": "caller", "text": "hi"}], lambda n, a: {"ok": True})
    assert "front desk" in out and agent.llm_calls == MAX_STEPS


def test_a_live_run_end_to_end_with_a_fake_model_uses_the_real_tools_and_rolls_back():
    scn = BY_ID["asks_for_human"]
    model = scripted_model(
        reply(
            calls=[
                {"id": "t", "name": "transfer_call", "arguments": json.dumps({"destination": "front_desk"})}
            ]
        ),
        reply("Of course. I'm connecting you to our front desk now."),
    )
    clinic = RealClinic(scn)
    try:
        record = run_scenario(scn, LiveAgent(model), ScriptedCaller(scn), clinic)
        assert record.final_state["transferred_to"] == "front_desk" and record.tool_calls[0].agent_turn == 1
    finally:
        clinic.close()


def test_a_real_clinic_scenario_leaves_no_rows_behind():
    import psycopg

    from app.settings import get_settings

    sql = "SELECT (SELECT count(*) FROM bookings), (SELECT count(*) FROM patients), (SELECT count(*) FROM call_sessions)"
    with psycopg.connect(get_settings().database_url) as c:
        before = c.execute(sql).fetchone()
        clinic = RealClinic(BY_ID["reschedule_appointment"])
        clinic.call("find_my_appointment", {"phone": "+15125550177"})
        clinic.close()
        assert c.execute(sql).fetchone() == before


def test_real_clinic_seeds_the_existing_booking_with_the_fixed_id_and_reports_state():
    scn = BY_ID["reschedule_appointment"]
    clinic = RealClinic(scn)
    try:
        assert clinic.final_state()["bookings_active"] == 1
        found = clinic.call("find_my_appointment", {"phone": "+15125550177"})
        assert found["bookings"][0]["booking_id"] == 7 and "Friday, October sixteenth" in found["say"]
        assert clinic.call("transfer_call", {"destination": "nowhere"})["ok"] is False
    finally:
        clinic.close()


def test_the_faq_tool_now_finds_hours_from_the_word_closing():
    # Found by the real-tool eval: the stub matched "closing", the real keyword list only had "close".
    scn = BY_ID["off_topic_redirect"]
    clinic = RealClinic(scn)
    try:
        r = clinic.call("faq_lookup", {"question": "Friday closing time"})
        assert r["topic"] == "hours"
    finally:
        clinic.close()


def test_the_reference_agent_still_passes_everything_on_the_stub():
    assert all(r.passed_all for r in evaluate(list(BY_ID.values()), "reference", runs=1))
    assert StubClinic  # the stub remains the CI backend
