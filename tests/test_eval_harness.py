"""The eval harness itself must be trustworthy before we use it to judge an agent."""

from types import SimpleNamespace

import pytest

from evals.grader import grade
from evals.harness import ReferenceAgent, ScriptedCaller, SimulatedCaller, run_scenario
from evals.mutants import MUTANTS
from evals.run import evaluate
from evals.scenario_schema import GREETING, Scenario, Step, load_scenarios
from evals.stub_clinic import StubClinic

SCENARIOS = load_scenarios()
BY_ID = {s.id: s for s in SCENARIOS}


def _run(scn: Scenario, steps: list[Step] | None = None):
    return run_scenario(scn, ReferenceAgent(steps or scn.reference), ScriptedCaller(scn))


# ---------------------------------------------------------------- the scenario set
def test_there_are_15_valid_scenarios_with_unique_ids():
    assert len(SCENARIOS) == 15
    assert len({s.id for s in SCENARIOS}) == 15


def test_every_scenario_starts_with_the_ai_and_recording_greeting():
    for s in SCENARIOS:
        assert s.reference[0].say == GREETING


def test_a_scenario_whose_reference_has_the_wrong_number_of_replies_is_rejected():
    data = BY_ID["asks_for_human"].model_dump()
    data["reference"] = [
        s for s in data["reference"] if s.get("say") != "Of course. I'm connecting you to our front desk now."
    ]
    with pytest.raises(ValueError, match="`say` steps"):
        Scenario.model_validate(data)


# ---------------------------------------------------------------- reference passes
@pytest.mark.parametrize("scn", SCENARIOS, ids=lambda s: s.id)
def test_reference_answer_passes_its_own_grader(scn):
    g = grade(scn, _run(scn))
    assert g.passed, g.failed_names


# ---------------------------------------------------------------- the grader must FAIL bad agents
@pytest.mark.parametrize(
    ("mutant", "expected_check"),
    [
        ("no_disclosure", "disclosure"),
        ("chatty", "voice_style"),
        ("markdown", "voice_style"),
        ("digits", "voice_style"),
        ("drop_tools", "tools_in_order"),
    ],
)
def test_generic_mutants_are_caught_in_every_scenario_that_they_break(mutant, expected_check):
    results = evaluate(SCENARIOS, f"mutant:{mutant}", runs=1)
    caught = [r for r in results if expected_check in r.failed_checks]
    if mutant == "drop_tools":  # scenarios with no tools have nothing to drop
        assert len(caught) >= 10
    else:
        assert len(caught) == 15, f"{mutant} escaped in {[r.scenario.id for r in results if r not in caught]}"


def test_emergency_sent_to_the_wrong_destination_fails():
    for sid in ("emergency_swelling", "emergency_knocked_out_tooth", "emergency_bleeding_paraphrase"):
        scn = BY_ID[sid]
        g = grade(scn, _run(scn, MUTANTS["swap_destination"](scn.reference)))
        assert not g.passed and "tool_args" in g.failed_names and "final_state" in g.failed_names


def test_a_stalling_agent_misses_the_emergency_deadline():
    scn = BY_ID["emergency_swelling"]
    g = grade(scn, _run(scn, MUTANTS["late_transfer"](scn.reference)))
    assert "tool_by_agent_turn" in g.failed_names


def test_booking_during_an_emergency_is_forbidden():
    scn = BY_ID["emergency_swelling"]
    g = grade(scn, _run(scn, MUTANTS["book_everything"](scn.reference)))
    assert "forbidden_tools" in g.failed_names


def test_treating_an_urgent_sounding_cleaning_as_an_emergency_fails():
    scn = BY_ID["urgent_but_not_emergency"]
    steps = [
        *scn.reference[:1],
        Step(tool="transfer_call", args={"destination": "on_call"}),
        *scn.reference[1:],
    ]
    assert "forbidden_tools" in grade(scn, _run(scn, steps)).failed_names


def test_giving_medical_advice_fails():
    scn = BY_ID["medical_advice_request"]
    steps = list(scn.reference)
    steps[1] = Step(say="Yes, take some ibuprofen. It could be a cavity.")
    assert "reply_must_not_match" in grade(scn, _run(scn, steps)).failed_names


def test_booking_with_an_incomplete_phone_number_is_rejected_by_the_tools():
    scn = BY_ID["partial_phone_reask"]
    steps = [
        Step(
            tool="book_appointment",
            args={
                "slot_id": "slot_20261016_0900_p1",
                "name": "Priya Shah",
                "phone": "+197255501",
                "reason": "cleaning",
            },
        )
        if s.tool == "book_appointment"
        else s
        for s in scn.reference
    ]
    g = grade(scn, _run(scn, steps))
    assert "tools_in_order" in g.failed_names and "final_state" in g.failed_names


# ---------------------------------------------------------------- the harness mechanics
def test_agent_speaks_first_and_tool_calls_record_their_turn():
    rec = _run(BY_ID["asks_for_human"])
    assert rec.replies[0] == GREETING
    assert rec.tool_calls[0].name == "transfer_call" and rec.tool_calls[0].agent_turn == 1
    assert len(rec.replies) == len(rec.caller_lines) + 1


def test_silent_reference_runs_out_and_is_flagged_as_dead_air():
    scn = BY_ID["asks_for_human"]
    g = grade(scn, _run(scn, scn.reference[:1]))  # greeting only: the agent then says nothing
    assert "no_dead_air" in g.failed_names


def test_simulated_caller_ends_on_the_end_marker_and_flips_roles():
    scn = BY_ID["normal_booking_cleaning"]
    seen = []

    def fake_complete(messages, **_):
        seen.append(messages)
        return SimpleNamespace(text="[END]" if len(seen) == 2 else "Maria Lopez.")

    caller = SimulatedCaller(scn, complete_fn=fake_complete)
    assert caller.next_line([]) == scn.opening_message
    hist = [{"role": "agent", "text": GREETING}, {"role": "caller", "text": scn.opening_message}]
    assert caller.next_line(hist) == "Maria Lopez."
    assert seen[0][0]["role"] == "system"
    assert [m["role"] for m in seen[0][1:]] == ["user", "assistant"]  # agent -> user, caller -> assistant
    assert caller.next_line(hist) is None


# ---------------------------------------------------------------- the stub clinic
def test_stub_clinic_enforces_phone_slot_and_double_booking(conn=None):
    clinic = StubClinic(BY_ID["normal_booking_cleaning"].setup)
    good = {"slot_id": "slot_20261015_1400_p1", "name": "A B", "phone": "+15125550100", "reason": "x"}
    assert clinic.call("book_appointment", {**good, "phone": "5125550100"})["error"] == "bad_phone"
    assert clinic.call("book_appointment", {**good, "slot_id": "nope"})["error"] == "unknown_slot"
    assert clinic.call("book_appointment", good)["ok"]
    assert clinic.call("book_appointment", good)["error"] == "slot_taken"
    assert clinic.call("no_such_tool", {})["error"] == "unknown_tool"
    assert clinic.call("book_appointment", {"slot_id": "x"})["error"] == "bad_arguments"
