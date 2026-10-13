"""Emergency layer two: the server forces a transfer if the agent did not. Tested with a fake Vapi control endpoint."""

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.assistant_config import load_config
from app.emergency import detect, last_user_text, transfer_seen, valid_control_url
from app.settings import get_settings

SECRET = "test-secret"
CONTROL = "https://phone-call.example.vapi.ai/abc/control"


# ------------------------------------------------------------------ what counts as an emergency
@pytest.mark.parametrize(
    ("said", "rule"),
    [
        ("My face is swollen and I can barely open my mouth.", "facial_swelling"),
        ("there's swelling in my jaw", "facial_swelling"),
        ("I'm having trouble swallowing", "breathing_or_swallowing"),
        ("my son just got hit by a baseball and one of his teeth got knocked out", "knocked_out_tooth"),
        ("He fell off his bike and hit his mouth on the pavement", "trauma"),
        (
            "My husband took a fall and there's blood everywhere from his mouth, it's not stopping",
            ("trauma", "bleeding"),
        ),
        ("I have a fever and my tooth hurts a lot", "fever_with_tooth_pain"),
    ],
)
def test_emergency_phrasings_are_detected(said, rule):
    # some sentences are two emergencies at once (a fall AND bleeding); either label is a correct detection
    assert detect(said) in (rule if isinstance(rule, tuple) else (rule,))


@pytest.mark.parametrize(
    "said",
    [
        "I'd like to book a teeth cleaning",
        "I need a cleaning as soon as possible, it's urgent for me",
        "There is no swelling in my face, I just want a checkup",
        "What time do you close on Fridays?",
        "my tooth has a dull ache when I drink cold water",
        "I'm not having trouble breathing, I just want to book",
    ],
)
def test_ordinary_and_negated_phrasings_are_not_emergencies(said):
    assert detect(said) is None


def test_conversation_helpers_and_the_control_url_check():
    conv = [{"role": "assistant", "content": "hi"}, {"role": "user", "content": "my face is swollen"}]
    assert last_user_text(conv) == "my face is swollen" and not transfer_seen(conv)
    conv.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "transferCall"}}]})
    assert transfer_seen(conv)
    assert valid_control_url(CONTROL) and valid_control_url("https://vapi.ai/x")
    for bad in (
        None,
        "",
        "http://x.vapi.ai/c",
        "https://evil.com/c",
        "https://vapi.ai.evil.com/c",
        "https://x.vapi.ai.evil.com/c",
    ):
        assert not valid_control_url(bad)


# ------------------------------------------------------------------ the server
EMERGENCY = [
    {"role": "assistant", "content": "How can I help?"},
    {"role": "user", "content": "My face is swollen and I can barely open my mouth."},
]


def update(call_id, conversation, control_url=CONTROL):
    return {
        "message": {
            "type": "conversation-update",
            "call": {"id": call_id, "monitor": {"controlUrl": control_url}},
            "conversation": conversation,
        }
    }


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("EMERGENCY_GRACE_S", "0")  # no waiting in tests
    get_settings.cache_clear()
    sent: list[tuple[str, str, str]] = []
    client = TestClient(
        create_app(control=lambda url, number, message: (sent.append((url, number, message)), 200)[1])
    )
    created: list[str] = []

    def post(call_id, conversation, **kw):
        created.append(call_id)
        return client.post(
            "/vapi/events", json=update(call_id, conversation, **kw), headers={"X-Vapi-Secret": SECRET}
        )

    yield post, sent
    with psycopg.connect(get_settings().database_url) as conn:
        for cid in created:
            conn.execute("DELETE FROM call_sessions WHERE vapi_call_id = %s", (cid,))


def row(call_id):
    with psycopg.connect(get_settings().database_url) as conn:
        return conn.execute(
            "SELECT emergency_detected_at IS NOT NULL, transfer_seen, emergency_forced FROM call_sessions WHERE vapi_call_id = %s",
            (call_id,),
        ).fetchone()


def test_an_emergency_the_agent_does_not_transfer_is_transferred_by_the_server_to_the_on_call_number(harness):
    post, sent = harness
    assert post("t-em-1", EMERGENCY).status_code == 200
    cfg, _ = load_config()
    assert sent == [(CONTROL, cfg.transfer["on_call"].number, cfg.transfer["on_call"].message)]
    assert row("t-em-1") == (True, False, True)


def test_when_the_agent_transfers_by_itself_the_server_stays_out_of_it(harness):
    post, sent = harness
    handled = [
        *EMERGENCY,
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "transferCall"}}]},
    ]
    post("t-em-2", handled)
    assert sent == [] and row("t-em-2")[1] is True


def test_the_agent_transfers_during_the_grace_period_so_the_server_does_not(harness, monkeypatch):
    post, sent = harness
    import app.api.main as m

    real = m._force_if_needed

    def late(
        connect, call_id, url, control
    ):  # the agent's transfer shows up while the server is still waiting
        with connect() as conn:
            conn.execute("UPDATE call_sessions SET transfer_seen = true WHERE vapi_call_id = %s", (call_id,))
            conn.commit()
        return real(connect, call_id, url, control)

    monkeypatch.setattr(m, "_force_if_needed", late)
    post("t-em-3", EMERGENCY)
    assert sent == [] and row("t-em-3") == (True, True, False)


def test_many_updates_for_one_emergency_force_the_transfer_exactly_once(harness):
    post, sent = harness
    for _ in range(3):
        post("t-em-4", EMERGENCY)
    assert len(sent) == 1


def test_an_ordinary_call_and_a_negated_phrase_never_trigger_it(harness):
    post, sent = harness
    post("t-em-5", [{"role": "user", "content": "I'd like a cleaning please"}])
    post("t-em-6", [{"role": "user", "content": "there is no swelling in my face"}])
    assert sent == [] and row("t-em-5") == (False, False, False)


def test_a_control_url_that_is_not_vapi_is_never_called(harness):
    post, sent = harness
    post("t-em-7", EMERGENCY, control_url="https://evil.example.com/control")
    assert sent == []


def test_the_watch_can_be_turned_off(harness, monkeypatch):
    post, sent = harness
    monkeypatch.setenv("EMERGENCY_WATCH", "false")
    get_settings.cache_clear()
    post("t-em-8", EMERGENCY)
    assert sent == []


def test_a_call_the_server_transferred_is_recorded_as_an_emergency_transfer_and_flagged(conn):
    import json
    from pathlib import Path

    from app.postcall import ingest_report, process_call

    report = json.loads(
        (Path(__file__).parent / "fixtures/vapi/report_faq.json").read_text()
    )  # the tool log has no transfer
    report["message"]["call"]["id"] = "t-em-forced"
    ingest_report(conn, report)
    conn.execute("INSERT INTO call_sessions (vapi_call_id, emergency_forced) VALUES ('t-em-forced', true)")
    good = '{"summary": "x", "outcome": "transferred_emergency", "sentiment": "negative", "phone_read_back": null, "date_read_back": null, "task_completed": true}'
    from types import SimpleNamespace

    process_call(conn, "t-em-forced", lambda m, **k: SimpleNamespace(text=good, cost_usd=0.0))
    outcome, flags = conn.execute(
        "SELECT outcome, flags FROM calls WHERE vapi_call_id = 't-em-forced'"
    ).fetchone()
    assert outcome == "transferred_emergency" and "emergency_forced_by_server" in flags


def test_the_test_only_variant_really_has_no_emergency_rule_but_keeps_the_other_safety_rules():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "assistant"
    _, prompt = load_config(variant=root / "variants" / "l2_test_no_emergency_rule.yaml")
    lowered = prompt.lower()
    assert "on-call dentist" not in lowered and "test-only" in lowered
    assert "never give medical advice" in lowered and "front desk" in lowered
