import json

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
    return TestClient(create_app())


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


def test_hello_tool_returns_vapi_result_format_and_saves_the_payload(client, tmp_path):
    r = client.post("/vapi/tools", json=TOOL_CALL, headers={"X-Vapi-Secret": SECRET})
    assert r.status_code == 200
    assert r.json() == {"results": [{"toolCallId": "abc123", "result": "Hello Anu, nice to meet you."}]}
    assert len(list(tmp_path.glob("*_tool-calls.json"))) == 1


def test_function_style_tool_call_shape_is_also_accepted(client):
    body = {
        "message": {
            "toolCallList": [
                {"id": "z", "function": {"name": "hello", "arguments": json.dumps({"name": "Bo"})}}
            ]
        }
    }
    r = client.post("/vapi/tools", json=body, headers={"X-Vapi-Secret": SECRET})
    assert r.json()["results"][0]["result"].startswith("Hello Bo")


def test_unknown_tool_is_an_error_string_not_a_crash(client):
    body = {"message": {"toolCallList": [{"id": "q", "name": "nope", "parameters": {}}]}}
    r = client.post("/vapi/tools", json=body, headers={"X-Vapi-Secret": SECRET})
    assert r.status_code == 200 and r.json()["results"][0]["error"] == "unknown tool nope"


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
    assert r.json()["results"][0]["result"] == "Hello Anu, nice to meet you."
