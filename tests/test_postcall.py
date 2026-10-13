"""The post-call pipeline, tested on the three REAL recorded calls (FAQ, emergency transfer, booking)."""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapters.fakes import FakeCrm
from app.postcall import (
    analyze_transcript,
    clean_report,
    derive_outcome,
    disclosure_said,
    extract_facts,
    ingest_report,
    process_call,
    process_crm_outbox,
)

FIX = Path(__file__).parent / "fixtures/vapi"


def load(name):
    """A recorded real report with a FRESH call id, so tests never collide with real calls in the dev database."""
    report = json.loads((FIX / f"{name}.json").read_text())
    report["message"]["call"]["id"] = f"t-{name}-{uuid.uuid4().hex[:8]}"
    return report


def outbox_status(conn, vapi_call_id):
    row = conn.execute(
        "SELECT o.status FROM crm_outbox o JOIN calls c ON c.id = o.call_id WHERE c.vapi_call_id = %s",
        (vapi_call_id,),
    ).fetchone()
    return row[0] if row else None


def call_id(report):
    return report["message"]["call"]["id"]


GOOD = json.dumps({"summary": "Caller asked about Friday hours and insurance.", "outcome": "faq", "sentiment": "positive",
                   "phone_read_back": None, "date_read_back": None, "task_completed": True})  # fmt: skip


def fake_llm(*texts):
    """A fake model returning the given replies in order; records how many times it was asked."""
    it = iter(texts)

    def fn(messages, **kwargs):
        fn.calls += 1
        return SimpleNamespace(text=next(it), cost_usd=0.0005)

    fn.calls = 0
    return fn


# ------------------------------------------------------------------ what happened, from the phone log
def test_faq_call_facts_and_outcome():
    f = extract_facts(load("report_faq"))
    assert [t.name for t in f.tools] == ["faq_lookup", "faq_lookup", "endCall"] and all(t.ok for t in f.tools)
    assert derive_outcome(f) == "faq" and f.transfer_label is None


def test_emergency_transfer_is_mapped_from_the_dialled_number_to_on_call_and_flagged_failed():
    f = extract_facts(load("report_emergency"))
    assert f.transfer_label == "on_call" and f.transfer_status == "failed"
    assert derive_outcome(f) == "transferred_emergency"


def test_booking_call_found_the_booking_even_though_the_phone_was_typed_by_speech_to_text():
    f = extract_facts(load("report_booking"))
    names = [t.name for t in f.tools if t.ok]
    assert names == ["check_availability", "check_availability", "book_appointment", "endCall"]
    assert derive_outcome(f) == "booked"


def test_the_disclosure_check_reads_what_was_actually_spoken():
    assert disclosure_said(extract_facts(load("report_faq")))
    # The real emergency call's transcript reads "I'm an. Assistant": no AI in the stored text (heard fine by the owner).
    assert not disclosure_said(extract_facts(load("report_emergency")))


def test_a_call_where_the_caller_never_spoke_is_abandoned():
    assert (
        derive_outcome(extract_facts(load("end_of_call_report"))) == "abandoned"
    )  # the spike call used only "hello"


# ------------------------------------------------------------------ save first
def test_saving_a_report_twice_stores_one_call_and_one_set_of_turns(conn):
    report = load("report_booking")
    assert ingest_report(conn, report) == (call_id(report), True)
    assert ingest_report(conn, report) == (call_id(report), False)  # the replay changes nothing
    n = conn.execute("SELECT count(*) FROM calls WHERE vapi_call_id = %s", (call_id(report),)).fetchone()[0]
    turns = conn.execute(
        "SELECT count(*) FROM call_turns t JOIN calls c ON c.id = t.call_id WHERE c.vapi_call_id = %s",
        (call_id(report),),
    ).fetchone()[0]
    assert n == 1 and turns == 10


def test_the_stored_report_has_no_signed_urls_credentials_or_system_prompt():
    dirty = {
        "message": {
            "artifact": {
                "presignedLogUrl": "x",
                "messages": [
                    {"role": "system", "message": "SECRET PROMPT"},
                    {"role": "bot", "message": "see https://a.b/c"},
                ],
            },
            "recordingUrl": "https://x",
            "orgId": "o",
        }
    }
    text = json.dumps(clean_report(dirty))
    assert (
        "SECRET PROMPT" not in text
        and "presigned" not in text
        and "https://" not in text
        and "orgId" not in text
    )


# ------------------------------------------------------------------ the LLM summary, validated
def test_a_valid_reply_is_accepted_with_its_cost():
    analysis, cost, error = analyze_transcript("AI: hi", fake_llm(GOOD))
    assert analysis and analysis.outcome == "faq" and cost == 0.0005 and error is None


def test_an_invalid_reply_is_repaired_once_by_showing_the_model_its_mistake():
    llm = fake_llm("Sure! here you go", GOOD)
    analysis, _, error = analyze_transcript("AI: hi", llm)
    assert analysis is not None and llm.calls == 2 and error is None


def test_two_invalid_replies_give_up_with_an_error_instead_of_guessing():
    llm = fake_llm("nope", '{"summary": "x", "outcome": "party"}')
    analysis, _, error = analyze_transcript("AI: hi", llm)
    assert analysis is None and llm.calls == 2 and error


# ------------------------------------------------------------------ the whole pass
def stored(conn, name):
    report = load(name)
    ingest_report(conn, report)
    return call_id(report)


def test_faq_call_is_analysed_and_stored_with_scores(conn):
    cid = stored(conn, "report_faq")
    assert process_call(conn, cid, fake_llm(GOOD)) == "done"
    row = conn.execute(
        "SELECT outcome, sentiment, analysis_status, flags, eval_scores, cost_breakdown FROM calls WHERE vapi_call_id = %s",
        (cid,),
    ).fetchone()
    assert row[0] == "faq" and row[1] == "positive" and row[2] == "done" and row[3] == []
    assert row[4]["disclosure_said"] is True and row[4]["turn_latency_avg_ms"] > 0
    assert (
        row[5]["analysis_usd"] == 0.0005 and row[5]["total"] > 0
    )  # Vapi's cost split is kept, ours is added


def test_when_the_llm_disagrees_with_the_phone_log_the_log_wins_and_the_call_is_flagged(conn):
    cid = stored(conn, "report_faq")
    wrong = json.dumps({**json.loads(GOOD), "outcome": "booked"})
    process_call(conn, cid, fake_llm(wrong))
    outcome, flags = conn.execute(
        "SELECT outcome, flags FROM calls WHERE vapi_call_id = %s", (cid,)
    ).fetchone()
    assert outcome == "faq" and flags == ["outcome_mismatch:llm=booked,log=faq"]


def test_the_emergency_call_is_flagged_for_the_missing_disclosure_and_the_failed_transfer(conn):
    cid = stored(conn, "report_emergency")
    llm = fake_llm(
        json.dumps({**json.loads(GOOD), "outcome": "transferred_emergency", "task_completed": True})
    )
    process_call(conn, cid, llm)
    outcome, flags = conn.execute(
        "SELECT outcome, flags FROM calls WHERE vapi_call_id = %s", (cid,)
    ).fetchone()
    assert outcome == "transferred_emergency" and set(flags) == {"disclosure_missing", "transfer_failed"}


def test_a_silent_call_costs_no_llm_call_and_is_marked_done(conn):
    report = load("report_faq")
    report["message"]["call"]["id"] = "t-silent"
    report["message"]["endedReason"] = "call.in-progress.error-assistant-did-not-receive-customer-audio"
    report["message"]["artifact"]["messages"] = []
    ingest_report(conn, report)
    llm = fake_llm()
    assert process_call(conn, "t-silent", llm) == "done" and llm.calls == 0
    assert conn.execute("SELECT outcome, flags FROM calls WHERE vapi_call_id = 't-silent'").fetchone() == (
        "abandoned",
        ["no_audio"],
    )


def test_an_unusable_model_leaves_the_call_for_a_human_not_guessed(conn):
    cid = stored(conn, "report_faq")
    assert process_call(conn, cid, fake_llm("x", "y")) == "needs_review"
    assert conn.execute(
        "SELECT analysis_status, analysis_error IS NOT NULL FROM calls WHERE vapi_call_id = %s", (cid,)
    ).fetchone() == ("needs_review", True)


def test_processing_a_call_twice_does_not_analyse_it_twice(conn):
    cid = stored(conn, "report_faq")
    process_call(conn, cid, fake_llm(GOOD))
    llm = fake_llm()
    assert process_call(conn, cid, llm) == "done" and llm.calls == 0


# ------------------------------------------------------------------ the CRM note
def test_a_booking_call_queues_a_note_for_its_patient_and_survives_a_crm_outage(conn):
    cid = stored(conn, "report_booking")
    pid = conn.execute(
        "INSERT INTO patients (phone, name) VALUES ('+19995550124', 'Chest Patient') RETURNING id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO bookings (patient_id, provider_id, type_code, start_at, end_at, source_call_id)"
        " VALUES (%s, 3, 'cleaning', '2026-11-06 10:00-05', '2026-11-06 11:00-05', %s)", (pid, cid),
    )  # fmt: skip
    booked = json.dumps(
        {**json.loads(GOOD), "outcome": "booked", "phone_read_back": True, "date_read_back": True}
    )
    process_call(conn, cid, fake_llm(booked))
    assert outbox_status(conn, cid) == "pending"

    crm = FakeCrm()
    crm.fail = True
    assert process_crm_outbox(conn, crm) == {
        "done": 0,
        "retry": 1,
        "failed": 0,
    }  # CRM down: kept, attempt counted
    crm.fail = False
    assert process_crm_outbox(conn, crm) == {"done": 1, "retry": 0, "failed": 0}  # back up: delivered
    assert len(crm.notes) == 1 and "booked" in crm.notes[0][1]
    assert process_crm_outbox(conn, crm) == {"done": 0, "retry": 0, "failed": 0}  # nothing is sent twice


def test_a_note_is_given_up_on_after_five_failed_attempts(conn):
    cid = stored(conn, "report_booking")
    pid = conn.execute(
        "INSERT INTO patients (phone, name) VALUES ('+19995550125', 'X') RETURNING id"
    ).fetchone()[0]
    call_pk = conn.execute("SELECT id FROM calls WHERE vapi_call_id = %s", (cid,)).fetchone()[0]
    conn.execute("INSERT INTO crm_outbox (call_id, patient_id, note) VALUES (%s, %s, 'n')", (call_pk, pid))
    crm = FakeCrm()
    crm.fail = True
    results = [process_crm_outbox(conn, crm) for _ in range(5)]
    assert results[-1]["failed"] == 1 and outbox_status(conn, cid) == "failed"


def test_an_anonymous_call_has_no_crm_note(conn):
    cid = stored(conn, "report_faq")
    process_call(conn, cid, fake_llm(GOOD))
    assert outbox_status(conn, cid) is None


# ------------------------------------------------------------------ analytics and bench
def test_analytics_computes_latency_cost_and_check_rates_from_the_stored_calls(conn):
    from app.analytics import percentile, summary
    from app.bench import render, task_success

    for name in ("report_faq", "report_emergency", "report_booking"):
        report = load(name)
        for m in report["message"]["artifact"][
            "messages"
        ]:  # tag the calls so they are isolated from real data
            if "assistantName" in m:
                m["assistantName"] = "p11-test-assistant"
        ingest_report(conn, report)
        process_call(conn, call_id(report), fake_llm(GOOD))
    s = summary(conn, assistant="p11-test-assistant")
    assert s["calls"] == 3 and s["turns"] == 15 and s["minutes"] == pytest.approx(2.75, abs=0.05)
    assert s["latency_ms"]["p50"] and s["latency_ms"]["p95"] >= s["latency_ms"]["p50"]
    assert s["cost_usd"]["total"] == pytest.approx(0.0693 + 0.0278 + 0.1384, abs=1e-3)
    assert s["cost_per_minute"]["vapi"] > s["cost_per_minute"]["stt"]
    assert (
        s["checks"]["disclosure_said"] == "2/3"
    )  # the emergency call's transcript lacks the word AI (a transcription artifact; the check can only read the transcript)
    assert "transfer_failed" in s["flags"] and "disclosure_missing" in s["flags"]
    assert "p50" not in render(s, "pending") or "ms" in render(s, "pending")
    assert task_success(Path("/nonexistent.csv")).startswith("pending")
    assert percentile([5, 1, 3, 2, 4], 50) == 3 and percentile([], 95) is None


def test_the_runbook_covers_all_15_scenarios_and_only_core_rows_count_toward_task_success(tmp_path):
    from app.bench import task_success
    from evals.scenario_schema import load_scenarios
    from scripts.make_runbook import render

    scenarios = load_scenarios()
    text = render(scenarios)
    assert text.count("\n## Call ") == 15 and all(s.id in text for s in scenarios)
    assert "must NOT use" in text and "transfers right away" in text
    sheet = tmp_path / "r.csv"
    sheet.write_text(
        "n,kind,scenario_id,vapi_call_id,success,notes\n1,core,a,,yes,\n2,core,b,,no,\n3,core,c,,,\n4,extra,x,,yes,\n"
    )
    assert task_success(sheet) == "1/2"  # unscored rows and the extra call are ignored
