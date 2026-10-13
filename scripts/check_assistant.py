"""Read-only: is what Vapi holds the same as assistant/prompt.md and receptionist.yaml? (nothing is changed)

Why it exists: the dashboard shows a per-user DRAFT or an older published version, so it can look different from
what the API holds. This asks the API directly and compares exactly.

Usage: uv run python -m scripts.check_assistant
"""

import sys
from typing import Any

import httpx

from app.assistant_config import load_config
from app.settings import get_settings


def check(client: httpx.Client, name: str) -> dict[str, Any]:
    cfg, local_prompt = load_config()
    found = [a for a in client.get("/assistant").json() if a["name"] == name]
    if not found:
        return {"found": False}
    a = found[0]
    live_prompt = (a["model"]["messages"][0]["content"]).strip()
    versions = client.get(f"/assistant/{a['id']}/version").json().get("results", [])
    return {
        "found": True,
        "latest_version": a.get("latestVersion"),
        "prompt_matches_local": live_prompt == local_prompt.strip(),
        "live_prompt_words": len(live_prompt.split()),
        "local_prompt_words": len(local_prompt.split()),
        "first_message_matches": a.get("firstMessage") == cfg.first_message,
        "tool_count": len(a["model"].get("toolIds", [])),
        "model": f"{a['model']['provider']}/{a['model']['model']}",
        "voice_set": bool(a.get("voice")),
        "transcriber_set": bool(a.get("transcriber")),
        "versions_newest_first": [v["createdAt"] for v in versions],
    }


def main() -> int:
    cfg, _ = load_config()
    s = get_settings()
    client = httpx.Client(
        base_url="https://api.vapi.ai",
        headers={"Authorization": f"Bearer {s.vapi_api_key.get_secret_value()}"},
        timeout=20,
    )
    r = check(client, cfg.name)
    if not r["found"]:
        print(f"assistant '{cfg.name}' not found in Vapi: run make sync-assistant-apply")
        return 1
    ok = r["prompt_matches_local"] and r["first_message_matches"]
    print(
        f"assistant: {cfg.name} | latest version: {r['latest_version']} | model {r['model']} | tools {r['tool_count']}"
    )
    print(f"prompt: live {r['live_prompt_words']} words, local {r['local_prompt_words']} words -> "
          f"{'IDENTICAL' if r['prompt_matches_local'] else 'DIFFERENT (run make sync-assistant-apply)'}")  # fmt: skip
    print(
        f"first message matches config: {r['first_message_matches']} | voice set: {r['voice_set']} | transcriber set: {r['transcriber_set']}"
    )
    print(f"versions (newest first): {', '.join(r['versions_newest_first'][:4])}")
    print("\nIf the dashboard shows something different, it is showing a draft or an older version:")
    print(
        "select the version number under the assistant's name > View Full History > Restore the newest one."
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
