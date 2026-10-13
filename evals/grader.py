"""The examiner with a checklist. Plain code, no LLM: free, repeatable, and easy to defend on a client call.

One run -> a list of named checks. A run passes only if every check passes. A scenario passes pass^k only
if ALL k runs pass (reliability, not luck).
"""

import re
from dataclasses import dataclass
from typing import Any

from evals.harness import RunRecord, ToolCallRecord
from evals.scenario_schema import Scenario

# Voice rules that apply to EVERY reply of EVERY scenario (PLAN: "write for the ear").
NO_MARKDOWN = re.compile(r"[*#`]|^\s*[-•]\s|\n")
NO_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
NO_DIGITS = re.compile(r"\d")  # numbers must be written as words so the voice reads them naturally
AI_WORD = re.compile(r"\bAI\b|artificial intelligence", re.IGNORECASE)
RECORDING_WORD = re.compile(r"record", re.IGNORECASE)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Grade:
    scenario_id: str
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_names(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]


def _arg_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str) and expected.startswith("re:"):
        return re.search(expected[3:], str(actual)) is not None
    return expected == actual


def _first_ok(calls: list[ToolCallRecord], name: str) -> ToolCallRecord | None:
    return next((c for c in calls if c.name == name and c.ok), None)


def _is_subsequence(wanted: list[str], seen: list[str]) -> bool:
    it = iter(seen)
    return all(any(w == s for s in it) for w in wanted)


def grade(scenario: Scenario, record: RunRecord) -> Grade:
    e = scenario.expect
    calls = record.tool_calls
    checks: list[Check] = []

    # 1. disclosure: the very first thing the agent says (ADR 001/003; EU AI Act Art. 50)
    first = record.replies[0] if record.replies else ""
    checks.append(
        Check("disclosure", bool(AI_WORD.search(first) and RECORDING_WORD.search(first)), first[:80])
    )

    # 2. tools
    ok_names = [c.name for c in calls if c.ok]
    checks.append(Check("tools_in_order", _is_subsequence(e.tools_in_order, ok_names), f"saw {ok_names}"))
    called = sorted({c.name for c in calls} & set(e.forbidden_tools))
    checks.append(Check("forbidden_tools", not called, f"called {called}"))
    working = [c for c in calls if c.name != "end_call"]  # hanging up after goodbye is not wandering
    checks.append(
        Check(
            "max_tool_calls",
            len(working) <= e.max_tool_calls,
            f"{len(working)} calls, max {e.max_tool_calls}",
        )
    )

    bad_args = []
    for tool, expected_args in e.tool_args.items():
        call = _first_ok(calls, tool)
        if call is None:
            bad_args.append(f"{tool}: never called successfully")
            continue
        for key, want in expected_args.items():
            if not _arg_matches(want, call.args.get(key)):
                bad_args.append(f"{tool}.{key}: wanted {want!r}, got {call.args.get(key)!r}")
    checks.append(Check("tool_args", not bad_args, "; ".join(bad_args)))

    late = []
    for tool, deadline in e.tool_by_agent_turn.items():
        call = _first_ok(calls, tool)
        if call is None or call.agent_turn > deadline:
            late.append(f"{tool}: turn {call.agent_turn if call else 'never'}, deadline {deadline}")
    checks.append(Check("tool_by_agent_turn", not late, "; ".join(late)))

    # 3. what the clinic looks like at the end
    wrong = [
        f"{k}: wanted {v!r}, got {record.final_state.get(k)!r}"
        for k, v in e.final_state.items()
        if record.final_state.get(k) != v
    ]
    checks.append(Check("final_state", not wrong, "; ".join(wrong)))

    # 4. what was said
    missing = [p for p in e.reply_must_match if not any(re.search(p, r) for r in record.replies)]
    checks.append(Check("reply_must_match", not missing, f"never said: {missing}"))
    present = [p for p in e.reply_must_not_match if any(re.search(p, r) for r in record.replies)]
    checks.append(Check("reply_must_not_match", not present, f"said: {present}"))

    # 5. voice style, every reply
    problems = []
    for i, reply in enumerate(record.replies):
        words = len(reply.split())
        if words > e.max_words_per_reply:
            problems.append(f"turn {i}: {words} words")
        if NO_MARKDOWN.search(reply):
            problems.append(f"turn {i}: markdown or newline")
        if NO_URL.search(reply):
            problems.append(f"turn {i}: url")
        if NO_DIGITS.search(reply):
            problems.append(f"turn {i}: digits (write numbers as words)")
    checks.append(Check("voice_style", not problems, "; ".join(problems)))

    # 6. the agent must answer every caller line (a silent turn is dead air)
    silent = [i for i, r in enumerate(record.replies) if not r.strip()]
    checks.append(Check("no_dead_air", not silent, f"empty replies at turns {silent}"))

    return Grade(scenario.id, checks)
