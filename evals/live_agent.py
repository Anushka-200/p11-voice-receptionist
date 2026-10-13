"""The real receptionist brain for text evals: our prompt and our tool definitions, driven through LiteLLM.

Analogy: a rehearsal. The live call uses Vapi's model; this runs the SAME prompt and the SAME tool descriptions
in plain text, so we can test the logic 45 times for cents instead of burning call minutes. It does not test the
voice or the latency (the 15 live web calls do), and the text model may differ from the live one (ADR 002).
"""

import json
from collections.abc import Callable
from typing import Any

from app.assistant_config import function_tool_payloads, load_config
from app.greeting import GREETING
from evals.harness import CallTool, History

MAX_STEPS = 6  # tool round trips per spoken turn: a runaway agent is cut off, like the 8-call cap in P03

_TRANSFER = {
    "type": "function",
    "function": {
        "name": "transfer_call",
        "description": "Transfer the call to a person. ALWAYS pass destination. on_call = the on-call dentist for dental "
        "emergencies; front_desk = the caller asks for a person or you cannot help.",
        "parameters": {
            "type": "object",
            "properties": {"destination": {"type": "string", "enum": ["on_call", "front_desk"]}},
            "required": ["destination"],
        },
    },
}
_END_CALL = {
    "type": "function",
    "function": {
        "name": "end_call",
        "description": "Hang up after the caller says goodbye.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def tool_schemas() -> list[dict[str, Any]]:
    cfg, _ = load_config()
    schemas = [
        {"type": "function", "function": t["function"]}
        for t in function_tool_payloads(cfg, "https://x", "x").values()
    ]
    return [*schemas, _TRANSFER, _END_CALL]


class LiveAgent:
    def __init__(self, complete_fn: Callable[..., Any] | None = None) -> None:
        if complete_fn is None:
            from app import llm  # imported lazily: litellm is slow to import and scripted runs never need it

            complete_fn = llm.complete
        self.complete_fn = complete_fn
        cfg, prompt = load_config()
        self.max_tokens = cfg.model.max_tokens
        self.transfer_messages = {label: d.message for label, d in cfg.transfer.items()}
        self.tools = tool_schemas()
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
        self.cost_usd = 0.0
        self.llm_calls = 0

    def turn(self, history: History, call_tool: CallTool) -> str:
        if not history:  # the greeting is Vapi's first message, not a model call
            self.messages.append({"role": "assistant", "content": GREETING})
            return GREETING
        # Keep our own full message list (tool calls and results included): `history` has only spoken text,
        # and the model must remember the slot ids it was just given.
        self.messages.append({"role": "user", "content": history[-1]["text"]})
        spoken: list[str] = []
        for _ in range(MAX_STEPS):
            res = self.complete_fn(
                self.messages, name="live-agent", max_tokens=self.max_tokens, tools=self.tools
            )
            self.llm_calls += 1
            self.cost_usd += res.cost_usd or 0.0
            self.messages.append(res.message or {"role": "assistant", "content": res.text})
            # Text the model writes ALONGSIDE a tool call is spoken aloud too (Vapi plays it as it calls the tool):
            # the prompt asks for exactly that in an emergency ("say one short sentence and transfer").
            if res.text and res.text.strip():
                spoken.append(res.text.strip())
            if not res.tool_calls:
                return " ".join(spoken)
            for call in res.tool_calls:
                args = json.loads(call["arguments"] or "{}")
                result = call_tool(call["name"], args)
                self.messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)}
                )
            for call in res.tool_calls:
                if call["name"] == "transfer_call":  # Vapi's transfer tool announces the handover itself
                    dest = json.loads(call["arguments"] or "{}").get("destination")
                    if dest in self.transfer_messages:
                        spoken.append(self.transfer_messages[dest])
            if any(c["name"] in ("end_call", "transfer_call") for c in res.tool_calls):
                return " ".join(spoken)  # on Vapi the call ends or is handed over here: no more model turns
        return "I'm sorry, let me connect you with the front desk."  # too many steps: fail safe, never silent
