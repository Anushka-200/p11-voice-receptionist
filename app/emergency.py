"""Layer two of the emergency rule: the server watches the call and forces a transfer if the agent did not.

Analogy: the receptionist is layer one. A second person is listening on the line. If a caller says "my face is swollen"
and nobody has transferred the call within a few seconds, the second person presses the transfer button.

Why it exists: layer one is a prompt, and a prompt depends on a model. A safety rule should not depend on that alone.
Nothing here runs in the model's path, so it cannot slow a normal call down.
"""

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

logger = logging.getLogger(__name__)
CONFIG = Path(__file__).resolve().parents[1] / "config" / "emergency.yaml"


@lru_cache
def _rules() -> tuple[list[tuple[str, list[re.Pattern[str]]]], re.Pattern[str], int]:
    data = yaml.safe_load(CONFIG.read_text())
    rules = [(r["id"], [re.compile(p, re.IGNORECASE) for p in r["patterns"]]) for r in data["rules"]]
    return rules, re.compile(data["negations"], re.IGNORECASE), int(data["negation_window_chars"])


def detect(text: str) -> str | None:
    """The id of the first emergency rule the caller's words match, or None. Negated phrases ("no swelling") are skipped."""
    rules, negation, window = _rules()
    for rule_id, patterns in rules:
        for pattern in patterns:
            for m in pattern.finditer(text):
                before = text[max(0, m.start() - window) : m.start()]
                if not (negation.search(before) or negation.search(m.group())):
                    return rule_id
    return None


def last_user_text(conversation: list[dict[str, Any]]) -> str:
    for msg in reversed(conversation):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            return str(msg["content"])
    return ""


def transfer_seen(conversation: list[dict[str, Any]]) -> bool:
    """Did the agent call the transfer tool? (It shows up as an assistant message carrying a transferCall tool call.)"""
    for msg in conversation:
        for call in msg.get("tool_calls") or []:
            if (call.get("function") or {}).get("name") == "transferCall":
                return True
    return False


def valid_control_url(url: str | None) -> bool:
    """Only ever POST to Vapi itself: the URL comes from an event, so it is checked before it is trusted."""
    if not url:
        return False
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "vapi.ai" or host.endswith(".vapi.ai"))


def post_transfer(control_url: str, number: str, message: str, client: httpx.Client | None = None) -> int:
    """Tell Vapi to transfer the live call. Returns the HTTP status (logged by the caller)."""
    body = {"type": "transfer", "destination": {"type": "number", "number": number}, "content": message}
    return (client or httpx.Client()).post(control_url, json=body, timeout=5.0).status_code
