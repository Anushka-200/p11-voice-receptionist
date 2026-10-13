import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.settings import get_settings

SECRET = "test-secret"
TOOL_CALL = {
    "message": {
        "type": "tool-calls",
        "toolCallList": [{"id": "abc123", "name": "hello", "parameters": {"name": "Anu"}}],
    }
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SPIKE_PAYLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield TestClient(create_app())
    import psycopg

    with psycopg.connect(get_settings().database_url) as conn:  # the API tests use the real dev database
        conn.execute("DELETE FROM tool_invocations WHERE vapi_call_id IN ('unknown-call', 'x')")
        conn.execute("DELETE FROM call_sessions WHERE vapi_call_id = 'unknown-call'")


def test_health_needs_no_secret(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"X-Vapi-Secret": "wrong"}])
def test_missing_or_wrong_secret_is_rejected_on_every_vapi_endpoint(client, headers):
    assert client.post("/vapi/tools", json=TOOL_CALL, headers=headers).status_code == 401
    assert client.post("/vapi/events", json={"message": {"type": "x"}}, headers=headers).status_code == 401


def test_an_unset_secret_rejects_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    c = TestClient(create_app())
    assert c.post("/vapi/tools", json=TOOL_CALL, headers={"X-Vapi-Secret": ""}).status_code == 401


def test_hello_tool_returns_vapi_result_format(client):
    r = client.post("/vapi/tools", json=TOOL_CALL, headers={"X-Vapi-Secret": SECRET})
    assert r.status_code == 200
    assert r.json()["results"][0]["toolCallId"] == "abc123"
    assert json.loads(r.json()["results"][0]["result"])["say"] == "Hello Anu, nice to meet you."


def test_function_style_tool_call_shape_is_also_accepted(client):
    body = {
        "message": {
            "toolCallList": [
                {"id": "z", "function": {"name": "hello", "arguments": json.dumps({"name": "Bo"})}}
            ]
        }
    }
    r = client.post("/vapi/tools", json=body, headers={"X-Vapi-Secret": SECRET})
    assert json.loads(r.json()["results"][0]["result"])["say"].startswith("Hello Bo")


def test_unknown_tool_is_an_error_string_not_a_crash(client):
    body = {"message": {"toolCallList": [{"id": "q", "name": "nope", "parameters": {}}]}}
    r = client.post("/vapi/tools", json=body, headers={"X-Vapi-Secret": SECRET})
    result = json.loads(r.json()["results"][0]["result"])
    assert r.status_code == 200 and result["ok"] is False and result["error"] == "unknown_tool"


def test_events_are_saved_by_type(client, tmp_path):
    r = client.post(
        "/vapi/events", json={"message": {"type": "end-of-call-report"}}, headers={"X-Vapi-Secret": SECRET}
    )
    assert r.status_code == 200
    assert len(list(tmp_path.glob("*_end-of-call-report.json"))) == 1


def test_a_real_vapi_tool_call_payload_is_handled(client):
    # Recorded from the Phase 0 spike call (identifiers stripped): arguments arrive as a dict, not a string.
    from pathlib import Path

    real = json.loads((Path(__file__).parent / "fixtures/vapi/tool_calls.json").read_text())
    r = client.post("/vapi/tools", json=real, headers={"X-Vapi-Secret": SECRET})
    assert r.status_code == 200
    assert json.loads(r.json()["results"][0]["result"])["say"] == "Hello Anu, nice to meet you."


def _vapi_call(client, call_id, name, args):
    body = {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id},
            "toolCallList": [{"id": "x", "type": "function", "function": {"name": name, "arguments": args}}],
        }
    }
    r = client.post("/vapi/tools", json=body, headers={"X-Vapi-Secret": SECRET})
    assert r.status_code == 200
    return json.loads(r.json()["results"][0]["result"])


def test_booking_flow_through_the_http_endpoint_then_a_replay_is_harmless(client):
    """The main flow end to end, committed to the real dev database and then cleaned up."""
    import psycopg

    call_id = "t-api-flow"
    try:
        # A Wednesday clock is not injectable over HTTP, so ask for a far-future weekday that always has openings.
        offered = _vapi_call(
            client, call_id, "check_availability", {"when_text": "next Monday", "appointment_type": "checkup"}
        )
        assert offered["ok"] and offered["slots"], offered
        slot = offered["slots"][0]["slot_id"]
        args = {
            "slot_id": slot,
            "name": "Api Tester",
            "phone": "nine nine nine, five five five, zero one zero nine",
            "reason": "checkup",
        }
        first = _vapi_call(client, call_id, "book_appointment", args)
        again = _vapi_call(client, call_id, "book_appointment", args)
        assert first["ok"] and again["replayed"] and first["booking_id"] == again["booking_id"]
        with psycopg.connect(get_settings().database_url) as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM bookings WHERE source_call_id = %s", (call_id,)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM tool_invocations WHERE vapi_call_id = %s", (call_id,)
                ).fetchone()[0]
                == 3
            )
    finally:
        with psycopg.connect(get_settings().database_url) as conn:
            for sql in (
                "DELETE FROM outbound_messages WHERE patient_id IN (SELECT id FROM patients WHERE phone = '+19995550109')",
                "DELETE FROM calendar_events WHERE booking_id IN (SELECT id FROM bookings WHERE source_call_id = 't-api-flow')",
                "DELETE FROM bookings WHERE source_call_id = 't-api-flow'",
                "DELETE FROM patients WHERE phone = '+19995550109'",
                "DELETE FROM idempotency_keys WHERE created_at > now() - interval '5 minutes' AND tool = 'book_appointment'",
                "DELETE FROM tool_invocations WHERE vapi_call_id = 't-api-flow'",
                "DELETE FROM call_sessions WHERE vapi_call_id = 't-api-flow'",
            ):
                conn.execute(sql)


def test_an_end_of_call_report_is_saved_first_and_analysed_once_even_if_vapi_sends_it_twice(
    monkeypatch, tmp_path
):
    """The real recorded booking report through the HTTP door; the analysis step is a spy (no LLM, no cost)."""
    import psycopg

    from app.api.main import create_app as build

    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SPIKE_PAYLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    analysed: list[str] = []
    c = TestClient(build(analyze=lambda conn, vid: analysed.append(vid)))
    report = json.loads((Path(__file__).parent / "fixtures/vapi/report_booking.json").read_text())
    vid = report["message"]["call"]["id"]
    try:
        first = c.post("/vapi/events", json=report, headers={"X-Vapi-Secret": SECRET}).json()
        again = c.post("/vapi/events", json=report, headers={"X-Vapi-Secret": SECRET}).json()
        assert first == {"ok": True, "call": vid, "stored": True} and again["stored"] is False
        assert analysed == [vid]  # the replay did not trigger a second analysis
        with psycopg.connect(get_settings().database_url) as conn:
            assert (
                conn.execute("SELECT count(*) FROM calls WHERE vapi_call_id = %s", (vid,)).fetchone()[0] == 1
            )
    finally:
        with psycopg.connect(get_settings().database_url) as conn:
            conn.execute("DELETE FROM calls WHERE vapi_call_id = %s", (vid,))


def test_other_events_are_acknowledged_but_not_stored_as_calls(client):
    r = client.post(
        "/vapi/events", json={"message": {"type": "status-update"}}, headers={"X-Vapi-Secret": SECRET}
    )
    assert r.json() == {"ok": True}
