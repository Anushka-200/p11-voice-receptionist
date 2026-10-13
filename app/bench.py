"""`make bench`: the results tables for docs/benchmarks.md, computed from the stored calls (nothing typed by hand).

Task success is the one number that needs a human: after each test call you record pass/fail in
docs/test_calls_results.csv (columns: n, scenario_id, vapi_call_id, success, notes), and it is read here.
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

import psycopg

from app.analytics import summary
from app.settings import get_settings

RESULTS_CSV = Path(__file__).resolve().parents[1] / "docs" / "test_calls_results.csv"


def task_success(path: Path = RESULTS_CSV) -> str:
    if not path.exists():
        return "pending (no results file)"
    # Only the 15 core scripted calls count toward task success; the extra calls (noise, silence, interruption) do not.
    rows = [
        r
        for r in csv.DictReader(path.open())
        if (r.get("success") or "").strip() and r.get("kind", "core") == "core"
    ]
    if not rows:
        return "pending (no calls scored yet)"
    ok = sum(r["success"].strip().lower() in ("y", "yes", "pass", "1", "true") for r in rows)
    return f"{ok}/{len(rows)}"


def render(s: dict[str, Any], success: str) -> str:
    lat, cpm, stage = s["latency_ms"], s["cost_per_minute"], s["stage_avg_ms"]
    f = lambda v, unit="": "n/a" if v is None else f"{v:g}{unit}"  # noqa: E731
    lines = [
        f"calls: {s['calls']} | minutes: {s['minutes']} | turns measured: {s['turns']}",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Turn latency p50 / p95 / mean | {f(lat['p50'], ' ms')} / {f(lat['p95'], ' ms')} / {f(lat['mean'], ' ms')} |",
        "| Stage averages (ms): " + ", ".join(f"{k.replace('Latency', '')} {f(v)}" for k, v in stage.items()) + " | |",
        f"| Cost per minute (total) | {f(cpm.get('total'), ' USD')} |",
        "| Cost per minute by stage | " + ", ".join(f"{k} {f(cpm.get(k))}" for k in ("vapi", "llm", "tts", "stt")) + " |",
        f"| Analysis cost (our post-call LLM) | {f(s['cost_usd'].get('analysis_usd'), ' USD')} total |",
        f"| Task success on scored test calls | {success} |",
        f"| Outcomes | {s['outcomes']} |",
        f"| Online-eval checks (passed/known) | {s['checks']} |",
        f"| Flags | {s['flags'] or 'none'} |",
    ]  # fmt: skip
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--assistant",
        default="p11-receptionist",
        help="which assistant's calls to summarise (a variant name for an experiment)",
    )
    args = parser.parse_args()
    with psycopg.connect(get_settings().database_url) as conn:
        print(f"assistant: {args.assistant}")
        print(render(summary(conn, args.assistant), task_success()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
