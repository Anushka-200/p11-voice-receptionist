"""Plays one scenario: a caller talks, an agent answers and calls tools, and everything is recorded.

The agent and the caller are plain interfaces, so the same loop runs:
- the ReferenceAgent (model answer from the YAML) + a scripted caller: deterministic, free, used in CI;
- later (Phase 3) the real prompt-driven agent + a simulated (LLM) caller: realistic, costs cents.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from evals.scenario_schema import Scenario, Step
from evals.stub_clinic import StubClinic

History = list[dict[str, str]]  # [{"role": "caller" | "agent", "text": "..."}]
CallTool = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass
class ToolCallRecord:
    name: str
    args: dict[str, Any]
    result: dict[str, Any]
    agent_turn: int  # 0 = the greeting, 1 = the answer to the caller's first line, ...

    @property
    def ok(self) -> bool:
        return bool(self.result.get("ok"))


@dataclass
class RunRecord:
    scenario_id: str
    caller_lines: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    final_state: dict[str, Any] = field(default_factory=dict)


class Agent(Protocol):
    def turn(self, history: History, call_tool: CallTool) -> str:
        """Do whatever the agent does this turn (any number of tool calls) and return what it SAYS."""


class Caller(Protocol):
    def next_line(self, history: History) -> str | None:
        """The next thing the caller says, or None when the call is over."""


class ReferenceAgent:
    """Replays the scenario's hand-written model answer: tool steps run, a `say` step ends the turn."""

    def __init__(self, steps: list[Step]) -> None:
        self.steps = list(steps)
        self.cursor = 0

    def turn(self, history: History, call_tool: CallTool) -> str:
        while self.cursor < len(self.steps):
            step = self.steps[self.cursor]
            self.cursor += 1
            if step.tool is not None:
                call_tool(step.tool, step.args)
            else:
                assert step.say is not None
                return step.say
        return ""  # the model answer ran out: silence, which the grader will notice


class ScriptedCaller:
    def __init__(self, scenario: Scenario) -> None:
        self.lines = [scenario.opening_message, *scenario.script]
        self.i = 0

    def next_line(self, history: History) -> str | None:
        if self.i >= len(self.lines):
            return None
        self.i += 1
        return self.lines[self.i - 1]


class SimulatedCaller:
    """An LLM plays the persona. Used with a real agent; costs a little per call."""

    END = "[END]"

    def __init__(
        self,
        scenario: Scenario,
        complete_fn: Callable[..., Any] | None = None,
        max_turns: int = 12,
    ) -> None:
        self.scenario = scenario
        if complete_fn is None:
            # Imported only now: litellm takes a long time to import, and scripted runs (CI) never need it.
            from app import llm

            complete_fn = llm.complete
        self.complete_fn = complete_fn
        self.max_turns = max_turns
        self.turns = 0
        self.cost_usd = 0.0

    def next_line(self, history: History) -> str | None:
        if self.turns == 0:
            self.turns = 1
            return self.scenario.opening_message
        if self.turns >= self.max_turns:
            return None
        system = (
            f"You are role-playing a phone caller to a dental clinic. Persona: {self.scenario.persona} "
            f"Your goal: {self.scenario.goal} "
            "Speak like a real caller: one or two short sentences, no lists, only what the receptionist asked. "
            f"When your goal is done or you have been transferred, reply with exactly {self.END}."
        )
        messages = [{"role": "system", "content": system}]
        for h in history:  # roles flip: the agent is the "user" from the simulated caller's point of view
            role = "user" if h["role"] == "agent" else "assistant"
            messages.append({"role": role, "content": h["text"]})
        res = self.complete_fn(messages, name="simulated-caller", max_tokens=80)
        self.cost_usd += getattr(res, "cost_usd", 0) or 0.0
        text = res.text.strip()
        if self.END in text:
            return None
        self.turns += 1
        return text


def run_scenario(scenario: Scenario, agent: Agent, caller: Caller, clinic: Any = None) -> RunRecord:
    """`clinic` is the backend (the stub by default; RealClinic runs the real tools)."""
    clinic = clinic or StubClinic(scenario.setup)
    record = RunRecord(scenario_id=scenario.id)
    history: History = []
    turn = 0

    def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = clinic.call(name, args)
        record.tool_calls.append(ToolCallRecord(name, args, result, turn))
        return result

    def agent_speaks() -> None:
        reply = agent.turn(history, call_tool)
        history.append({"role": "agent", "text": reply})
        record.replies.append(reply)

    agent_speaks()  # the agent always speaks first: that is where the AI + recording disclosure goes
    while (line := caller.next_line(history)) is not None:
        turn += 1
        history.append({"role": "caller", "text": line})
        record.caller_lines.append(line)
        agent_speaks()

    record.final_state = clinic.final_state()
    return record
