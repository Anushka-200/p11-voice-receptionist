"""Numbers about the calls we have taken: latency, cost per minute by stage, outcomes, and the online-eval checks.

One function feeds both the Streamlit dashboard and `make bench`, so the README table and the dashboard can never
disagree. Everything is computed from the stored Vapi reports (nothing is estimated).
"""

import math
from collections import Counter
from typing import Any

import psycopg

STAGES = ("transcriberLatency", "modelLatency", "voiceLatency", "endpointingLatency")
COST_PARTS = ("stt", "llm", "tts", "vapi", "transport")


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile: the smallest value with at least p percent of the data at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def summary(conn: psycopg.Connection, assistant: str | None = "p11-receptionist") -> dict[str, Any]:
    where, params = ("WHERE c.assistant_name = %s", (assistant,)) if assistant else ("", ())
    calls = conn.execute(
        "SELECT c.outcome, c.duration_s, c.cost_breakdown, c.flags, c.eval_scores, c.analysis_status"
        f" FROM calls c {where} ORDER BY c.id", params,
    ).fetchall()  # fmt: skip
    turns = conn.execute(
        "SELECT t.latency_ms, t.stage_ms FROM call_turns t JOIN calls c ON c.id = t.call_id "
        f"{where}{' AND' if where else 'WHERE'} t.latency_ms > 0", params,
    ).fetchall()  # fmt: skip

    minutes = sum(float(c[1] or 0) for c in calls) / 60
    cost = {
        k: sum(float((c[2] or {}).get(k) or 0) for c in calls) for k in (*COST_PARTS, "total", "analysis_usd")
    }
    latencies = [float(t[0]) for t in turns]
    stage_avg = {}
    for stage in (
        STAGES
    ):  # turns where a stage did not run report 0 for it: averaging them in would hide the real cost
        vals = [float(t[1][stage]) for t in turns if t[1] and t[1].get(stage)]
        stage_avg[stage] = round(sum(vals) / len(vals), 1) if vals else None
    scores = [c[4] for c in calls if c[4]]

    def rate(key: str) -> str:
        known = [s[key] for s in scores if s.get(key) is not None]
        return f"{sum(bool(v) for v in known)}/{len(known)}" if known else "n/a"

    return {
        "calls": len(calls),
        "minutes": round(minutes, 2),
        "outcomes": dict(Counter(c[0] or "unanalysed" for c in calls)),
        "needs_review": sum(c[5] == "needs_review" for c in calls),
        "turns": len(latencies),
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "mean": round(sum(latencies) / len(latencies), 1) if latencies else None,
        },  # fmt: skip
        "stage_avg_ms": stage_avg,
        "cost_usd": {k: round(v, 4) for k, v in cost.items()},
        "cost_per_minute": {k: round(v / minutes, 4) for k, v in cost.items() if k != "analysis_usd"}
        if minutes
        else {},
        "checks": {
            k: rate(k) for k in ("disclosure_said", "phone_read_back", "date_read_back", "task_completed")
        },
        "flags": dict(Counter(f.split(":")[0] for c in calls for f in (c[3] or []))),
    }
