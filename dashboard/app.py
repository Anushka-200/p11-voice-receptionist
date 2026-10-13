"""Call analytics dashboard (Streamlit). Reads Postgres only; every number comes from app.analytics.

Run: make dashboard   ->  http://localhost:8511
"""

import json

import pandas as pd
import psycopg
import streamlit as st

from app.analytics import summary
from app.settings import get_settings

st.set_page_config(page_title="Receptionist call analytics", layout="wide")
st.title("AI receptionist: call analytics")
st.caption("Bright Smile Dental (fake clinic). All numbers come from stored Vapi call reports.")

conn = psycopg.connect(get_settings().database_url)
names = [
    r[0]
    for r in conn.execute(
        "SELECT DISTINCT assistant_name FROM calls WHERE assistant_name IS NOT NULL ORDER BY 1"
    )
]
if not names:
    st.info("No calls stored yet. Run `python -m scripts.backfill_reports` or make a call.")
    st.stop()
assistant = st.sidebar.selectbox(
    "Assistant", names, index=names.index("p11-receptionist") if "p11-receptionist" in names else 0
)
s = summary(conn, assistant)

a, b, c, d = st.columns(4)
a.metric("Calls", s["calls"], f"{s['minutes']} min")
b.metric(
    "Turn latency p50",
    f"{s['latency_ms']['p50'] or 0:.0f} ms",
    f"p95 {s['latency_ms']['p95'] or 0:.0f} ms",
    delta_color="off",
)
c.metric("Cost per minute", f"${s['cost_per_minute'].get('total', 0):.3f}")
d.metric("Needs review", s["needs_review"])

left, right = st.columns(2)
with left:
    st.subheader("Outcomes")
    st.bar_chart(pd.Series(s["outcomes"], name="calls"))
    st.subheader("Online-eval checks (passed / known)")
    st.table(pd.DataFrame([s["checks"]], index=["calls"]).T)
with right:
    st.subheader("Where the time goes (average ms per stage)")
    st.bar_chart(
        pd.Series({k.replace("Latency", ""): v or 0 for k, v in s["stage_avg_ms"].items()}, name="ms")
    )
    st.subheader("Cost per minute by stage (USD)")
    st.table(pd.DataFrame([{k: v for k, v in s["cost_per_minute"].items() if v}], index=["USD/min"]).T)
if s["flags"]:
    st.warning("Flags: " + ", ".join(f"{k} x{v}" for k, v in s["flags"].items()))

st.subheader("Calls")
rows = conn.execute(
    "SELECT id, started_at, outcome, sentiment, duration_s, (cost_breakdown->>'total')::numeric, flags, analysis_status"
    " FROM calls WHERE assistant_name = %s ORDER BY id DESC", (assistant,),
).fetchall()  # fmt: skip
df = pd.DataFrame(
    rows, columns=["id", "started", "outcome", "sentiment", "seconds", "cost_usd", "flags", "status"]
)
st.dataframe(df, use_container_width=True, hide_index=True)

if rows:
    st.subheader("Call detail")
    pick = st.selectbox("Call", [r[0] for r in rows])
    summary_text, transcript, scores, raw = conn.execute(
        "SELECT summary, transcript, eval_scores, raw_report FROM calls WHERE id = %s", (pick,)
    ).fetchone()  # fmt: skip
    st.write(summary_text)
    st.json(scores or {})
    st.text(transcript or "(no transcript)")
    tools = [
        m
        for m in (raw or {}).get("message", {}).get("artifact", {}).get("messages", [])
        if m.get("role") in ("tool_calls", "tool_call_result")
    ]
    if tools:
        st.caption("Tool log")
        st.code(json.dumps(tools, indent=1)[:4000])
