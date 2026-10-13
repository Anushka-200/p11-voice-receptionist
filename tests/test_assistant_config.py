"""The assistant-as-code config is complete, consistent with the server, and the sync script behaves."""

import json
import re

import httpx
import pytest

from app.assistant_config import (
    AssistantConfig,
    assistant_payload,
    function_tool_payloads,
    load_config,
    transfer_tool_payload,
)
from app.greeting import GREETING
from app.tools import TOOLS
from scripts.sync_assistant import Vapi, apply, build_all, main

BASE = "https://example-tunnel.trycloudflare.com"
CRED = "cred-123"


@pytest.fixture(scope="module")
def loaded():
    return load_config()


def test_config_loads_and_the_first_message_is_the_shared_greeting(loaded):
    cfg, prompt = loaded
    assert (
        cfg.first_message == GREETING
        and "AI assistant" in cfg.first_message
        and "recorded" in cfg.first_message
    )
    assert cfg.limits["max_duration_seconds"] <= 600  # unbounded-consumption control


def test_every_server_tool_has_a_description_and_a_schema_from_its_argument_model(loaded):
    cfg, _ = loaded
    tools = function_tool_payloads(cfg, BASE, CRED)
    assert set(tools) == set(TOOLS) and set(cfg.tool_messages) <= set(TOOLS)
    assert tools["faq_lookup"]["messages"] == []  # fast tool: no filler
    assert tools["check_availability"]["messages"][0]["type"] == "request-start"
    book = tools["book_appointment"]["function"]["parameters"]
    assert set(book["required"]) == {"slot_id", "name", "phone"} and "title" not in book
    assert all(p.get("description") for p in book["properties"].values())  # the model reads these
    for t in tools.values():
        assert t["server"] == {"url": f"{BASE}/vapi/tools", "credentialId": CRED}


def test_transfer_has_both_destinations_with_valid_numbers(loaded):
    cfg, _ = loaded
    assert set(cfg.transfer) == {"on_call", "front_desk"}
    descriptions = " ".join(d["description"] for d in transfer_tool_payload(cfg)["destinations"])
    assert "[on_call]" in descriptions and "[front_desk]" in descriptions


def test_a_bad_transfer_number_or_a_silence_ladder_that_never_ends_is_rejected(loaded):
    cfg, _ = loaded
    data = cfg.model_dump()
    data["transfer"]["on_call"]["number"] = "911"
    with pytest.raises(ValueError, match="transfer numbers"):
        AssistantConfig.model_validate(data)
    data = cfg.model_dump()
    data["silence_ladder"][-1]["end_call"] = False
    with pytest.raises(ValueError, match="silence ladder"):
        AssistantConfig.model_validate(data)


def test_assistant_payload_has_the_safety_and_cost_limits_and_all_events(loaded):
    cfg, prompt = loaded
    p = assistant_payload(cfg, prompt, ["t1", "t2"], BASE, CRED)
    assert p["firstMessage"] == GREETING and p["firstMessageMode"] == "assistant-speaks-first"
    assert p["model"]["toolIds"] == ["t1", "t2"] and p["model"]["messages"][0]["content"] == prompt
    assert p["server"] == {"url": f"{BASE}/vapi/events", "credentialId": CRED}
    assert {"end-of-call-report", "tool-calls", "user-interrupted"} <= set(p["serverMessages"])
    assert p["maxDurationSeconds"] <= 600 and p["model"]["maxTokens"] <= 300
    assert p["voice"]["provider"] and p["transcriber"]["model"]  # explicit, not left to Vapi defaults
    assert p["transcriber"]["fallbackPlan"]["autoFallback"]["enabled"] is True  # provider-outage fallback
    last = p["hooks"][-1]["do"]
    assert last[-1] == {"type": "tool", "tool": {"type": "endCall"}}  # the ladder ends by hanging up


def test_the_prompt_carries_the_non_negotiable_rules_and_names_real_tools(loaded):
    _, prompt = loaded
    lowered = prompt.lower()
    for must in ("nine one one", "on-call dentist", "front desk", "never give medical advice", "ignore any request",
                 "read the phone number back", "never guess a date"):  # fmt: skip
        assert must in lowered, must
    for name in re.findall(
        r"\b(check_availability|book_appointment|find_my_appointment|reschedule_appointment|cancel_appointment|faq_lookup)\b",
        prompt,
    ):
        assert name in TOOLS
    assert len(prompt.split()) <= 600  # the prompt is paid for on every turn (E4 trims it)


# ------------------------------------------------------------------ the sync script
def test_dry_run_sends_nothing_and_needs_an_https_url(monkeypatch, capsys):
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("VAPI_CREDENTIAL_ID", CRED)
    from app.settings import get_settings

    get_settings.cache_clear()
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN: nothing was sent" in out and "--apply" in out
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8011")
    get_settings.cache_clear()
    with pytest.raises(SystemExit, match="https"):
        main([])


def _fake_vapi(existing_tools, existing_assistants):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        assert req.headers["authorization"] == "Bearer key"
        if req.method == "GET" and req.url.path == "/tool":
            return httpx.Response(200, json=existing_tools)
        if req.method == "GET" and req.url.path == "/assistant":
            return httpx.Response(200, json=existing_assistants)
        return httpx.Response(200, json={"id": f"id-{len(calls)}"})

    return Vapi(
        "key", httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.vapi.ai")
    ), calls


def test_apply_creates_what_is_missing_updates_what_exists_and_never_deletes_or_touches_the_spike(loaded):
    built = build_all(BASE, CRED)
    spike_tool = {"id": "spike-hello", "type": "function", "function": {"name": "hello"}}
    spike_assistant = {"id": "spike-asst", "name": "p11-spike"}
    existing_book = {"id": "old-book", "type": "function", "function": {"name": "book_appointment"}}
    vapi, calls = _fake_vapi([spike_tool, existing_book], [spike_assistant])
    ids = apply(vapi, built, BASE, CRED)
    methods = [(m, p) for m, p, _ in calls if m != "GET"]
    assert ("PATCH", "/tool/old-book") in methods  # existing owned tool: updated
    assert (
        sum(1 for m, p in methods if (m, p) == ("POST", "/tool")) == len(built["tools"]) - 1
    )  # the rest: created
    assert ("POST", "/assistant") in methods  # no assistant with our name yet: created, not spike-asst
    assert not any(m == "DELETE" for m, _ in methods)
    assert not any("spike" in p for _, p in methods)
    patched = next(b for m, p, b in calls if p == "/tool/old-book")
    assert "type" not in patched  # PATCH must not resend the immutable type
    assert "assistant" in ids


def test_apply_updates_the_assistant_when_it_already_exists(loaded):
    built = build_all(BASE, CRED)
    vapi, calls = _fake_vapi([], [{"id": "mine", "name": built["cfg"].name}])
    apply(vapi, built, BASE, CRED)
    assert ("PATCH", "/assistant/mine") in [(m, p) for m, p, _ in calls]
