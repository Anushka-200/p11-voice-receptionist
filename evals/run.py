"""`make eval`: run every scenario k times and print pass^k.

Three kinds of agent:
  reference         replays the model answers in the YAML (proves the harness and grader; free, used in CI)
  mutant:<name>     a deliberately broken copy of it (the grader must fail it)
  live              the real prompt + tools through the LLM, a simulated caller, and the REAL tools on the database
                    (rolled back after every scenario). Costs cents, so it asks first.

Usage:
  uv run python -m evals.run --runs 3
  uv run python -m evals.run --agent mutant:no_disclosure
  uv run python -m evals.run --agent live --runs 3
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.grader import Grade, grade
from evals.harness import ReferenceAgent, ScriptedCaller, SimulatedCaller, run_scenario
from evals.mutants import MUTANTS
from evals.scenario_schema import Scenario, load_scenarios

RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass
class ScenarioResult:
    scenario: Scenario
    grades: list[Grade]
    cost_usd: float = 0.0

    @property
    def passes(self) -> int:
        return sum(g.passed for g in self.grades)

    @property
    def passed_all(self) -> bool:  # pass^k
        return self.passes == len(self.grades)

    @property
    def failed_checks(self) -> list[str]:
        return sorted({n for g in self.grades for n in g.failed_names})


def _show(scenario: Scenario, record: Any, g: Grade) -> None:
    print(f"\n--- {scenario.id}: {'PASS' if g.passed else 'FAIL ' + str(g.failed_names)}", file=sys.stderr)
    for c in g.checks:
        if not c.passed:
            print(f"    check {c.name}: {c.detail}", file=sys.stderr)
    for i, reply in enumerate(record.replies):
        calls = [
            f"{t.name}({json.dumps(t.args)[:70]}) -> ok={t.ok}"
            for t in record.tool_calls
            if t.agent_turn == i
        ]
        line = record.caller_lines[i - 1] if i else "(call starts)"
        print(f"  caller: {line}\n  agent : {reply!r}", file=sys.stderr)
        for tool_line in calls:
            print(f"      tool: {tool_line}", file=sys.stderr)
    print(file=sys.stderr, flush=True)


def _one_run(
    scenario: Scenario, agent_name: str, complete_fn: Any, verbose: bool = False
) -> tuple[Grade, float]:
    """One scenario run -> (grade, cost). Self-contained, so runs can go in parallel (own DB connection)."""
    if agent_name == "live":
        from app.llm import LLMError
        from evals.grader import Check
        from evals.live_agent import LiveAgent
        from evals.real_clinic import RealClinic

        agent, caller, clinic = (
            LiveAgent(complete_fn),
            SimulatedCaller(scenario, complete_fn),
            RealClinic(scenario),
        )
        try:
            record = run_scenario(scenario, agent, caller, clinic)
        except LLMError as exc:  # a failed model call is a failed run with a clear reason, not a crashed eval
            return Grade(scenario.id, [Check("llm_error", False, str(exc))]), agent.cost_usd + caller.cost_usd
        finally:
            clinic.close()
        g = grade(scenario, record)
        if verbose and not g.passed:
            _show(scenario, record, g)
        return g, agent.cost_usd + caller.cost_usd
    steps = scenario.reference
    if agent_name.startswith("mutant:"):
        steps = MUTANTS[agent_name.split(":", 1)[1]](steps)
    elif agent_name != "reference":
        raise SystemExit(f"unknown agent {agent_name!r}")
    return grade(scenario, run_scenario(scenario, ReferenceAgent(steps), ScriptedCaller(scenario))), 0.0


def evaluate(
    scenarios: list[Scenario],
    agent_name: str = "reference",
    runs: int = 3,
    complete_fn: Any = None,
    workers: int = 1,
    progress: bool = False,
    verbose: bool = False,
) -> list[ScenarioResult]:
    """`complete_fn` lets tests drive the live agent with a fake model (no network, no cost).

    `workers` > 1 runs (scenario, run) pairs in parallel threads: live runs spend their time waiting on the
    network, so four at once is about four times faster. Progress goes to stderr so a long run is never silent.
    """
    jobs = [(i, scenario) for i, scenario in enumerate(scenarios) for _ in range(runs)]
    done: list[tuple[int, Grade, float]] = []
    started = time.monotonic()

    def work(job: tuple[int, Scenario]) -> tuple[int, Grade, float]:
        i, scenario = job
        g, cost = _one_run(scenario, agent_name, complete_fn, verbose)
        if progress:
            print(
                f"[{len(done) + 1}/{len(jobs)}] {'PASS' if g.passed else 'FAIL'} {scenario.id} "
                f"({time.monotonic() - started:.0f}s elapsed)"
                + ("" if g.passed else f" failed: {g.failed_names}"),
                file=sys.stderr,
                flush=True,
            )
        return i, g, cost

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for fut in as_completed([pool.submit(work, j) for j in jobs]):
                done.append(fut.result())
    else:
        for j in jobs:
            done.append(work(j))

    results = []
    for i, scenario in enumerate(scenarios):
        mine = [(g, c) for k, g, c in done if k == i]
        results.append(ScenarioResult(scenario, [g for g, _ in mine], sum(c for _, c in mine)))
    return results


def report(results: list[ScenarioResult], agent_name: str, runs: int) -> str:
    lines = [f"agent: {agent_name} | runs per scenario: {runs}", ""]
    for r in results:
        status = "PASS" if r.passed_all else "FAIL"
        extra = f"  failed: {', '.join(r.failed_checks)}" if r.failed_checks else ""
        lines.append(f"{status}  {r.scenario.id:<34} {r.passes}/{runs}{extra}")
    n = len(results)
    ok = sum(r.passed_all for r in results)
    emergencies = [r for r in results if r.scenario.category == "emergency"]
    disclosed = sum(
        all(c.passed for g in r.grades for c in g.checks if c.name == "disclosure") for r in results
    )
    unreviewed = sum(not r.scenario.reviewed for r in results)
    lines += [
        "",
        f"pass^{runs}: {ok}/{n} scenarios",
        f"emergencies handled: {sum(r.passed_all for r in emergencies)}/{len(emergencies)}",
        f"AI + recording disclosure said: {disclosed}/{n}",
    ]
    if unreviewed:
        lines.append(
            f"WARNING: {unreviewed}/{n} scenarios are not owner-reviewed yet; read them before trusting numbers"
        )
    if agent_name == "reference":
        lines.append(
            "note: the reference agent replays the model answers, so this proves the harness and grader, not a model"
        )
    if agent_name == "live":
        total = sum(r.cost_usd for r in results)
        lines.append(
            f"cost: ${total:.4f} total, ${total / max(1, n * runs):.4f} per run "
            "(text model; the live voice call costs more)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", default="reference", help="reference | mutant:<name>")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--only", help="run just this scenario id")
    parser.add_argument(
        "--workers", type=int, default=1, help="run this many scenario runs in parallel (live: try 4)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print the transcript and tool calls of failing runs"
    )
    parser.add_argument("--yes", action="store_true", help="confirm a live run above the cost guard")
    args = parser.parse_args(argv)

    scenarios = load_scenarios()
    if args.only:
        scenarios = [s for s in scenarios if s.id == args.only]
    if args.agent == "live":
        from app.settings import get_settings

        settings = get_settings()
        if not settings.anthropic_api_key.get_secret_value():
            raise SystemExit(
                "ANTHROPIC_API_KEY is empty: create a key in its own workspace with a spend limit, put it in .env"
            )
        estimate = 0.006 * len(scenarios) * args.runs  # about 16 short Haiku-class calls per run
        print(
            f"live eval: {len(scenarios)} scenarios x {args.runs} runs, estimated cost about ${estimate:.2f}"
        )
        if estimate > settings.cost_confirm_above_usd and not args.yes:
            raise SystemExit(
                f"estimate is above ${settings.cost_confirm_above_usd:.2f}: re-run with --yes to confirm"
            )
    results = evaluate(
        scenarios,
        args.agent,
        args.runs,
        workers=args.workers,
        progress=args.agent == "live",
        verbose=args.verbose,
    )
    print(report(results, args.agent, args.runs))

    RESULTS_DIR.mkdir(exist_ok=True)
    out = {
        "agent": args.agent,
        "runs": args.runs,
        "scenarios": {r.scenario.id: {"passes": r.passes, "failed_checks": r.failed_checks} for r in results},
    }
    (RESULTS_DIR / f"latest-{args.agent.replace(':', '-')}.json").write_text(json.dumps(out, indent=2))
    # The reference must pass everything; a mutant is SUPPOSED to fail (that proves the grader works).
    failed = any(not r.passed_all for r in results)
    return 1 if (failed and args.agent in ("reference", "live")) else 0


if __name__ == "__main__":
    sys.exit(main())
