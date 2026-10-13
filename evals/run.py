"""`make eval`: run every scenario k times and print pass^k.

Phase 1 runs the REFERENCE agent (the model answers in the YAML) or a deliberately broken mutant of it.
That proves the harness and grader work. Phase 3 adds the real prompt-driven agent as `--agent live`.

Usage:
  uv run python -m evals.run --runs 3
  uv run python -m evals.run --agent mutant:no_disclosure
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from evals.grader import Grade, grade
from evals.harness import ReferenceAgent, ScriptedCaller, run_scenario
from evals.mutants import MUTANTS
from evals.scenario_schema import Scenario, load_scenarios

RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass
class ScenarioResult:
    scenario: Scenario
    grades: list[Grade]

    @property
    def passes(self) -> int:
        return sum(g.passed for g in self.grades)

    @property
    def passed_all(self) -> bool:  # pass^k
        return self.passes == len(self.grades)

    @property
    def failed_checks(self) -> list[str]:
        return sorted({n for g in self.grades for n in g.failed_names})


def evaluate(scenarios: list[Scenario], agent_name: str = "reference", runs: int = 3) -> list[ScenarioResult]:
    results = []
    for scenario in scenarios:
        steps = scenario.reference
        if agent_name.startswith("mutant:"):
            steps = MUTANTS[agent_name.split(":", 1)[1]](steps)
        elif agent_name != "reference":
            raise SystemExit(f"unknown agent {agent_name!r} (the live agent arrives in Phase 3)")
        grades = []
        for _ in range(runs):
            record = run_scenario(scenario, ReferenceAgent(steps), ScriptedCaller(scenario))
            grades.append(grade(scenario, record))
        results.append(ScenarioResult(scenario, grades))
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
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", default="reference", help="reference | mutant:<name>")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--only", help="run just this scenario id")
    args = parser.parse_args(argv)

    scenarios = load_scenarios()
    if args.only:
        scenarios = [s for s in scenarios if s.id == args.only]
    results = evaluate(scenarios, args.agent, args.runs)
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
    return 1 if (failed and args.agent == "reference") else 0


if __name__ == "__main__":
    sys.exit(main())
