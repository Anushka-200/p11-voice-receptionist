"""Push assistant/receptionist.yaml to Vapi: function tools, transfer, end-call, then the assistant.

Safe by default: with no flag it only PRINTS what it would send (a dry run, nothing leaves the machine).
`--apply` writes to your Vapi account. It creates or updates, never deletes, and it only touches objects it owns
(the six function tools by name, one transferCall, one endCall, and the assistant named in the config), so the
spike assistant `p11-spike` and its `hello` tool are left alone.

Usage:
  uv run python -m scripts.sync_assistant            # dry run
  uv run python -m scripts.sync_assistant --apply    # really push
"""

import argparse
import json
import sys
from typing import Any

import httpx

from app.assistant_config import (
    END_CALL_TOOL,
    assistant_payload,
    function_tool_payloads,
    load_config,
    transfer_tool_payload,
)
from app.settings import get_settings

API = "https://api.vapi.ai"


class Vapi:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self.http = client or httpx.Client(base_url=API, timeout=20)
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def _req(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        r = self.http.request(method, path, headers=self.headers, json=body)
        if r.status_code >= 400:
            raise SystemExit(f"Vapi {method} {path} failed: {r.status_code} {r.text[:400]}")
        return r.json()

    def list(self, path: str) -> list[dict[str, Any]]:
        return self._req("GET", path)

    def create(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._req("POST", path, body)

    def update(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._req("PATCH", path, body)


def _tool_key(tool: dict[str, Any]) -> str | None:
    """How we recognise a tool we own: function tools by name, the others by type."""
    if tool.get("type") == "function":
        return (tool.get("function") or {}).get("name")
    return tool.get("type")


def build_all(base_url: str, credential_id: str) -> dict[str, Any]:
    cfg, prompt = load_config()
    tools = function_tool_payloads(cfg, base_url, credential_id)
    tools["transferCall"] = transfer_tool_payload(cfg)
    tools["endCall"] = END_CALL_TOOL
    return {"cfg": cfg, "prompt": prompt, "tools": tools}


def apply(vapi: Vapi, built: dict[str, Any], base_url: str, credential_id: str) -> dict[str, str]:
    cfg, prompt = built["cfg"], built["prompt"]
    existing = {_tool_key(t): t["id"] for t in vapi.list("/tool") if _tool_key(t) in built["tools"]}
    ids: dict[str, str] = {}
    for key, payload in built["tools"].items():
        if key in existing:
            # PATCH rejects the immutable `type` field, so it is dropped for updates.
            body = {k: v for k, v in payload.items() if k != "type"}
            ids[key] = vapi.update(f"/tool/{existing[key]}", body)["id"]
        else:
            ids[key] = vapi.create("/tool", payload)["id"]
    body = assistant_payload(cfg, prompt, list(ids.values()), base_url, credential_id)
    current = {a["name"]: a["id"] for a in vapi.list("/assistant")}
    if cfg.name in current:
        ids["assistant"] = vapi.update(f"/assistant/{current[cfg.name]}", body)["id"]
    else:
        ids["assistant"] = vapi.create("/assistant", body)["id"]
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="really write to Vapi (default is a dry run)")
    args = parser.parse_args(argv)
    s = get_settings()
    base_url, credential_id = s.public_base_url, s.vapi_credential_id
    if not base_url.startswith("https://"):
        raise SystemExit("PUBLIC_BASE_URL must be the https tunnel URL (Vapi refuses http)")
    if not credential_id:
        raise SystemExit("VAPI_CREDENTIAL_ID is empty (the p11-webhook credential's id)")

    built = build_all(base_url, credential_id)
    tool_ids = [f"<id of {k}>" for k in built["tools"]]
    preview = assistant_payload(built["cfg"], built["prompt"], tool_ids, base_url, credential_id)
    print(f"tools to sync ({len(built['tools'])}): {', '.join(built['tools'])}")
    print(f"assistant '{built['cfg'].name}': model {built['cfg'].model.provider}/{built['cfg'].model.model}, "
          f"prompt {len(built['prompt'].split())} words, {len(preview['hooks'])} silence hooks")  # fmt: skip
    if not args.apply:
        print("\nDRY RUN: nothing was sent. Full payloads:\n")
        print(json.dumps({"tools": built["tools"], "assistant": preview}, indent=2))
        print("\nRun again with --apply to push to Vapi.")
        return 0
    ids = apply(Vapi(s.vapi_api_key.get_secret_value()), built, base_url, credential_id)
    print("synced:", json.dumps(ids, indent=2))
    print("Put the assistant id in .env as VAPI_ASSISTANT_ID if you want scripts to find it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
