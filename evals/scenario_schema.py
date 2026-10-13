# Adapted from P03 (p03-ai-operations-agent/evals/scenario_schema.py), extended for voice.
"""The shape of an eval scenario (evals/scenarios/*.yaml) and a loader that checks every file.

A scenario says WHAT should happen, never HOW the agent does it: which tools it must call (in order),
which it must never call, what the clinic must look like at the end, and what the spoken replies must
or must not contain. It also carries a hand-written reference call (the model answer), used to test
the grader itself and later as a gold example for the prompt.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.greeting import GREETING

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"

TOOLS = {
    "check_availability",
    "book_appointment",
    "find_my_appointment",
    "reschedule_appointment",
    "cancel_appointment",
    "faq_lookup",
    "transfer_call",
    "end_call",
}


class Step(BaseModel):
    """One step of the reference agent: either say something, or call a tool."""

    model_config = ConfigDict(extra="forbid")

    say: str | None = None
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("say")
    @classmethod
    def _greeting_placeholder(cls, v: str | None) -> str | None:
        return v.replace("{{GREETING}}", GREETING) if v else v

    @model_validator(mode="after")
    def _one_kind(self) -> "Step":
        if (self.say is None) == (self.tool is None):
            raise ValueError("a step is either `say` or `tool`")
        if self.tool is not None and self.tool not in TOOLS:
            raise ValueError(f"unknown tool {self.tool}")
        return self


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_phone: str | None = None  # a patient who already exists
    existing_booking: dict[str, Any] | None = None  # {id, type, label}


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools_in_order: list[str] = Field(
        default_factory=list, description="Successful calls, in this order (others may sit between)"
    )
    forbidden_tools: list[str] = Field(default_factory=list, description="Must NEVER be called at all")
    max_tool_calls: int = Field(default=8, description="Over this = the agent is wandering")
    tool_args: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Subset match on the first successful call of each tool; 're:<pattern>' = regex search",
    )
    tool_by_agent_turn: dict[str, int] = Field(
        default_factory=dict,
        description="The tool must be called at or before this agent turn (turn 0 = greeting). Emergencies: 1",
    )
    final_state: dict[str, Any] = Field(
        default_factory=dict, description="Checked against the clinic at the end"
    )
    reply_must_match: list[str] = Field(
        default_factory=list, description="Each regex must match at least one agent reply"
    )
    reply_must_not_match: list[str] = Field(
        default_factory=list, description="No agent reply may match any of these"
    )
    max_words_per_reply: int = Field(default=40, description="Voice turns are short")


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    category: Literal[
        "booking", "difficult", "ambiguity", "reschedule", "cancel", "faq", "transfer",
        "emergency", "not_emergency", "safety", "off_topic",
    ]  # fmt: skip
    reviewed: bool = False  # stays False until the OWNER has read it (P01/P02 lesson)
    persona: str
    goal: str
    opening_message: str
    script: list[str] = Field(default_factory=list, description="Caller lines after the opening line")
    setup: Setup = Field(default_factory=Setup)
    expect: Expect
    reference: list[Step]

    @model_validator(mode="after")
    def _reference_matches_script(self) -> "Scenario":
        # The agent speaks once at the start (greeting) and once after every caller line.
        says = sum(1 for s in self.reference if s.say is not None)
        needed = 2 + len(self.script)
        if says != needed:
            raise ValueError(f"{self.id}: reference has {says} `say` steps, the script needs {needed}")
        return self


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    scenarios = [
        Scenario.model_validate(yaml.safe_load(p.read_text())) for p in sorted(directory.glob("*.yaml"))
    ]
    ids = [s.id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario ids")
    return scenarios
