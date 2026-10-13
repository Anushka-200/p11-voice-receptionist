"""Build the Vapi tools and assistant from assistant/receptionist.yaml + prompt.md.

Analogy: the written employee handbook. The loader checks the handbook is complete, then turns it into the
exact forms Vapi wants. Tool definitions are generated from the same argument models our server validates with,
so what the model is told and what the server accepts cannot drift apart.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.greeting import GREETING
from app.tools import TOOLS

ASSISTANT_DIR = Path(__file__).resolve().parents[1] / "assistant"
E164 = re.compile(r"^\+1\d{10}$")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str
    temperature: float = Field(ge=0, le=1)
    max_tokens: int = Field(gt=0, le=1000)


class Speaking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_wait_seconds: float = Field(ge=0, le=5)
    stop_num_words: int = Field(ge=0)
    stop_voice_seconds: float = Field(ge=0)
    stop_backoff_seconds: float = Field(ge=0)


class SilenceStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after_seconds: int = Field(gt=0)
    say: str
    end_call: bool = False


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: str
    description: str
    message: str

    @field_validator("number")
    @classmethod
    def _e164(cls, v: str) -> str:
        if not E164.match(v):
            raise ValueError("transfer numbers must be +1 and ten digits")
        return v


class AssistantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    first_message: str
    prompt_file: str
    model: Model
    voice: dict[str, Any]
    transcriber: dict[str, Any]
    limits: dict[str, int]
    speaking: Speaking
    silence_ladder: list[SilenceStep]
    tool_descriptions: dict[str, str]
    tool_messages: dict[str, str]
    transfer: dict[str, Destination]
    server_messages: list[str]

    @field_validator("first_message")
    @classmethod
    def _greeting(cls, v: str) -> str:
        return GREETING if v == "{{GREETING}}" else v

    @field_validator("silence_ladder")
    @classmethod
    def _ladder(cls, steps: list[SilenceStep]) -> list[SilenceStep]:
        times = [s.after_seconds for s in steps]
        if times != sorted(times) or not steps[-1].end_call:
            raise ValueError("the silence ladder must get longer and end with end_call")
        return steps


def load_config(directory: Path = ASSISTANT_DIR) -> tuple[AssistantConfig, str]:
    cfg = AssistantConfig.model_validate(yaml.safe_load((directory / "receptionist.yaml").read_text()))
    missing = set(TOOLS) - set(cfg.tool_descriptions)
    if missing:
        raise ValueError(f"tools without a description: {sorted(missing)}")
    return cfg, (directory / cfg.prompt_file).read_text().strip()


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop the 'title' noise Pydantic adds; keep type, properties, required and descriptions."""
    if isinstance(schema, dict):
        return {k: _clean_schema(v) for k, v in schema.items() if k != "title"}
    return schema


def function_tool_payloads(
    cfg: AssistantConfig, base_url: str, credential_id: str
) -> dict[str, dict[str, Any]]:
    """One Vapi function tool per server tool, keyed by tool name."""
    out = {}
    for name, (args_model, _) in TOOLS.items():
        schema = _clean_schema(args_model.model_json_schema())
        out[name] = {
            "type": "function",
            "function": {"name": name, "description": cfg.tool_descriptions[name], "parameters": schema},
            "server": {"url": f"{base_url.rstrip('/')}/vapi/tools", "credentialId": credential_id},
            # A filler only for tools that touch the calendar. Fast tools (the FAQ) get none: a filler on a fast
            # answer sounds robotic. Experiment E5 tests a "say it only if it takes long" policy.
            "messages": (
                [{"type": "request-start", "content": cfg.tool_messages[name], "blocking": False}]
                if name in cfg.tool_messages
                else []
            ),
        }
    return out


def transfer_tool_payload(cfg: AssistantConfig) -> dict[str, Any]:
    return {
        "type": "transferCall",
        "destinations": [
            {
                "type": "number",
                "number": d.number,
                "message": d.message,
                "description": f"[{label}] {d.description}",
            }
            for label, d in cfg.transfer.items()
        ],
    }


END_CALL_TOOL = {"type": "endCall"}


def assistant_payload(
    cfg: AssistantConfig, prompt: str, tool_ids: list[str], base_url: str, credential_id: str
) -> dict[str, Any]:
    hooks = []
    for step in cfg.silence_ladder:
        actions: list[dict[str, Any]] = [{"type": "say", "exact": step.say}]
        if step.end_call:
            actions.append({"type": "tool", "tool": {"type": "endCall"}})
        hooks.append(
            {
                "on": "customer.speech.timeout",
                "options": {
                    "timeoutSeconds": step.after_seconds,
                    "triggerMaxCount": 1,
                    "triggerResetMode": "onUserSpeech",
                },
                "do": actions,
            }
        )
    sp = cfg.speaking
    return {
        "name": cfg.name,
        "firstMessage": cfg.first_message,
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": cfg.model.provider,
            "model": cfg.model.model,
            "temperature": cfg.model.temperature,
            "maxTokens": cfg.model.max_tokens,
            "messages": [{"role": "system", "content": prompt}],
            "toolIds": tool_ids,
        },
        "voice": cfg.voice,
        "transcriber": cfg.transcriber,
        "maxDurationSeconds": cfg.limits["max_duration_seconds"],
        "startSpeakingPlan": {"waitSeconds": sp.start_wait_seconds},
        "stopSpeakingPlan": {
            "numWords": sp.stop_num_words,
            "voiceSeconds": sp.stop_voice_seconds,
            "backoffSeconds": sp.stop_backoff_seconds,
        },
        "hooks": hooks,
        "server": {"url": f"{base_url.rstrip('/')}/vapi/events", "credentialId": credential_id},
        "serverMessages": cfg.server_messages,
    }
