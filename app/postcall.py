"""Everything that happens AFTER a call: save the report, work out what happened, summarise, sync the CRM.

Analogy: the receptionist's end-of-day notes. The recording of the day (Vapi's end-of-call report) is filed first,
untouched. Then someone reads what the phone log shows (which tools ran, was the caller transferred), writes a
summary, and double-checks their own opinion against the log. If they disagree, the log wins and the disagreement
is flagged for a human.

Order matters: SAVE FIRST (a replayed webhook is a no-op), analysis second (it can fail and be retried), CRM last
(a CRM outage must never lose a call record).
"""

import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, Field, ValidationError

from app.adapters.fakes import FakeCrm
from app.adapters.ports import AdapterUnavailable, CrmPort
from app.assistant_config import load_config
from app.phone import to_e164
from app.settings import get_settings

logger = logging.getLogger(__name__)

Outcome = Literal[
    "booked", "rescheduled", "cancelled", "faq", "transferred_human", "transferred_emergency", "abandoned"
]
NO_AUDIO = "did-not-receive-customer-audio"  # in Vapi's endedReason when the caller never spoke
AI_WORD = re.compile(r"\bAI\b|artificial intelligence", re.IGNORECASE)
RECORDING_WORD = re.compile(r"record", re.IGNORECASE)
_DROP = {
    "webCallUrl", "monitor", "orgId", "recordingUrl", "stereoRecordingUrl", "logUrl", "recording", "assistant",
    "assistantOverrides", "credentialId",
}  # fmt: skip


class CallAnalysis(BaseModel):
    """What the LLM reads out of the transcript. The outcome is cross-checked against the tool log by code."""

    summary: str = Field(
        max_length=700,
        description="Two or three plain sentences about what the caller wanted and what happened",
    )
    outcome: Outcome
    sentiment: Literal["positive", "neutral", "negative"]
    phone_read_back: bool | None = Field(
        description="Did the agent read the caller's phone number back? null if no phone was given"
    )
    date_read_back: bool | None = Field(
        description="Did the agent state the booked date and time back? null if nothing was booked"
    )
    task_completed: bool = Field(description="Did the caller get what they called for?")


# ------------------------------------------------------------------ 1. save first
def clean_report(body: dict[str, Any]) -> dict[str, Any]:
    """Drop expiring signed URLs, credentials and the (large) system prompt before storing."""

    def walk(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items() if not (k.startswith("presigned") or k in _DROP)}
        if isinstance(o, list):
            return [walk(x) for x in o if not (isinstance(x, dict) and x.get("role") == "system")]
        if isinstance(o, str):
            return re.sub(r"https?://\S+", "<url>", o)
        return o

    return walk(body)


def ingest_report(conn: psycopg.Connection, body: dict[str, Any]) -> tuple[str, bool]:
    """Store the raw report keyed by Vapi's call id. Returns (call id, newly stored). A replay changes nothing."""
    m = body["message"]
    vapi_id = m["call"]["id"]
    art = m.get("artifact") or {}
    cost = {k: v for k, v in (m.get("costBreakdown") or {}).items() if k != "analysisCostBreakdown"}
    names = [x.get("assistantName") for x in art.get("messages", []) if x.get("assistantName")]
    row = conn.execute(
        "INSERT INTO calls (vapi_call_id, raw_report, transcript, ended_reason, duration_s, cost_breakdown, started_at, assistant_name)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (vapi_call_id) DO NOTHING RETURNING id",
        (vapi_id, json.dumps(clean_report(body)), m.get("transcript") or art.get("transcript"), m.get("endedReason"),
         m.get("durationSeconds"), json.dumps(cost), m.get("startedAt"), names[0] if names else None),
    ).fetchone()  # fmt: skip
    if row is None:
        return vapi_id, False
    for i, t in enumerate((art.get("performanceMetrics") or {}).get("turnLatencies", []), start=1):
        stages = {
            k: t.get(k) for k in ("modelLatency", "voiceLatency", "transcriberLatency", "endpointingLatency")
        }
        conn.execute(
            "INSERT INTO call_turns (call_id, turn_no, latency_ms, stage_ms) VALUES (%s, %s, %s, %s)",
            (row[0], i, t.get("turnLatency"), json.dumps(stages)),
        )
    return vapi_id, True


# ------------------------------------------------------------------ 2. what happened (from the phone log)
@dataclass
class ToolEvent:
    name: str
    args: dict[str, Any]
    ok: bool


@dataclass
class CallFacts:
    ended_reason: str = ""
    duration_s: float = 0.0
    first_bot_message: str = ""
    user_turns: int = 0
    tools: list[ToolEvent] = field(default_factory=list)
    transfer_label: str | None = None  # on_call | front_desk, mapped from the number Vapi dialled
    transfer_status: str | None = None  # Vapi's own status; "failed" on a browser call


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def extract_facts(report: dict[str, Any]) -> CallFacts:
    m = report["message"] if "message" in report else report
    art = m.get("artifact") or {}
    facts = CallFacts(
        ended_reason=m.get("endedReason") or "", duration_s=float(m.get("durationSeconds") or 0)
    )
    cfg, _ = load_config()
    by_number = {d.number: label for label, d in cfg.transfer.items()}
    pending: dict[str, ToolEvent] = {}
    for msg in art.get("messages", []):
        role = msg.get("role")
        if role == "bot" and not facts.first_bot_message:
            facts.first_bot_message = msg.get("message", "")
        elif role == "user":
            facts.user_turns += 1
        elif role == "tool_calls":
            for call in msg.get("toolCalls", []):
                fn = call.get("function", {})
                args = _json(fn.get("arguments", {})) or {}
                event = ToolEvent(fn.get("name", ""), args if isinstance(args, dict) else {}, ok=False)
                pending[call.get("id", "")] = event
                facts.tools.append(event)
                if event.name == "transferCall":
                    facts.transfer_label = by_number.get(str(event.args.get("destination")), "front_desk")
        elif role == "tool_call_result":
            finished = pending.get(msg.get("toolCallId", ""))
            if finished is not None:
                result = _json(msg.get("result"))
                # Our tools return JSON with an "ok" flag; Vapi's built-ins (transferCall, endCall) return plain text.
                finished.ok = bool(result.get("ok")) if isinstance(result, dict) else True
    transfers = art.get("transfers") or []
    if transfers:
        facts.transfer_status = transfers[0].get("status")
    return facts


def derive_outcome(facts: CallFacts) -> Outcome:
    """The outcome according to the phone log alone. Priority: emergency, human, then what the tools completed."""
    if facts.transfer_label == "on_call":
        return "transferred_emergency"
    if facts.transfer_label:
        return "transferred_human"
    done = {t.name for t in facts.tools if t.ok}
    for tool, outcome in (("book_appointment", "booked"), ("reschedule_appointment", "rescheduled"),
                          ("cancel_appointment", "cancelled"), ("faq_lookup", "faq")):  # fmt: skip
        if tool in done:
            return outcome  # type: ignore[return-value]
    return "abandoned"


def disclosure_said(facts: CallFacts) -> bool:
    return bool(AI_WORD.search(facts.first_bot_message) and RECORDING_WORD.search(facts.first_bot_message))


# ------------------------------------------------------------------ 3. the LLM summary, validated
_SYSTEM = (
    "You analyse a phone call between a caller and the AI receptionist of a dental clinic. Reply with ONE JSON object "
    "and nothing else, with exactly these keys: summary (2-3 plain sentences), outcome (one of: booked, rescheduled, "
    "cancelled, faq, transferred_human, transferred_emergency, abandoned), sentiment (positive, neutral or negative, "
    "about the caller), phone_read_back (true/false/null: did the agent read the caller's phone number back), "
    "date_read_back (true/false/null: did the agent state the booked date and time back), task_completed (true/false). "
    "The transcript is speech-to-text and may contain mistakes; judge by meaning."
)


def _parse(text: str) -> CallAnalysis:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the reply")
    return CallAnalysis.model_validate(json.loads(match.group()))


def analyze_transcript(
    transcript: str, complete_fn: Callable[..., Any]
) -> tuple[CallAnalysis | None, float, str | None]:
    """One call, then one repair attempt with the error shown to the model. Returns (analysis, cost, error)."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Transcript:\n{transcript}"},
    ]
    cost, error = 0.0, None
    for attempt in range(2):
        res = complete_fn(messages, name="postcall-analysis", max_tokens=500)
        cost += getattr(res, "cost_usd", 0) or 0.0
        try:
            return _parse(res.text), cost, None
        except (ValueError, ValidationError) as exc:
            error = str(exc)[:300]
            if attempt == 0:  # repair-then-retry: show the model what was wrong, once
                messages += [
                    {"role": "assistant", "content": res.text},
                    {
                        "role": "user",
                        "content": f"That reply was not valid: {error}. Reply with only the JSON object.",
                    },
                ]
    return None, cost, error


# ------------------------------------------------------------------ 4. put it together
def process_call(
    conn: psycopg.Connection, vapi_call_id: str, complete_fn: Callable[..., Any] | None = None
) -> str:
    """Analyse one stored call. Returns the status: done | needs_review. Safe to run twice (done calls are skipped)."""
    row = conn.execute(
        "SELECT id, raw_report, transcript, analysis_status FROM calls WHERE vapi_call_id = %s",
        (vapi_call_id,),
    ).fetchone()
    if row is None:
        raise KeyError(vapi_call_id)
    call_id, raw, transcript, status = row
    if status == "done":
        return "done"
    facts = extract_facts(raw)
    derived = derive_outcome(facts)
    flags: list[str] = []
    scores: dict[str, Any] = {"disclosure_said": disclosure_said(facts), "derived_outcome": derived}
    if not scores["disclosure_said"] and facts.first_bot_message:
        flags.append("disclosure_missing")
    if facts.transfer_label and facts.transfer_status == "failed":
        flags.append("transfer_failed")  # expected on a browser call; a real problem on a phone line

    summary, sentiment, analysis_cost, error, outcome = "", None, 0.0, None, derived
    if NO_AUDIO in facts.ended_reason or facts.user_turns == 0:
        summary, flags, status_out = (
            "The caller never spoke; no analysis was needed.",
            [*flags, "no_audio"],
            "done",
        )
    else:
        if complete_fn is None:
            from app import llm  # imported lazily: litellm is slow to import and tests never need it

            complete_fn = llm.complete
        analysis, analysis_cost, error = analyze_transcript(transcript or "", complete_fn)
        if analysis is None:
            status_out = "needs_review"  # the model could not produce valid output twice; a human looks
        else:
            status_out = "done"
            summary, sentiment = analysis.summary, analysis.sentiment
            scores.update(llm_outcome=analysis.outcome, phone_read_back=analysis.phone_read_back,
                          date_read_back=analysis.date_read_back, task_completed=analysis.task_completed)  # fmt: skip
            if analysis.outcome != derived:  # the phone log wins; the disagreement is kept for a human
                flags.append(f"outcome_mismatch:llm={analysis.outcome},log={derived}")
    scores["turn_latency_avg_ms"] = _avg_latency(raw)

    conn.execute(
        "UPDATE calls SET summary = %s, outcome = %s, sentiment = %s, eval_scores = %s, flags = %s,"
        " analysis_status = %s, analysis_error = %s,"
        " cost_breakdown = COALESCE(cost_breakdown, '{}'::jsonb) || jsonb_build_object('analysis_usd', %s::numeric)"
        " WHERE id = %s",
        (
            summary,
            outcome,
            sentiment,
            json.dumps(scores),
            flags,
            status_out,
            error,
            round(analysis_cost, 6),
            call_id,
        ),
    )
    if status_out == "done" and summary:
        _queue_crm_note(conn, call_id, vapi_call_id, facts, outcome, summary)
    return status_out


def _avg_latency(raw: dict[str, Any]) -> float | None:
    m = raw.get("message", raw)
    turns = ((m.get("artifact") or {}).get("performanceMetrics") or {}).get("turnLatencies", [])
    vals = [t["turnLatency"] for t in turns if t.get("turnLatency")]
    return round(sum(vals) / len(vals), 1) if vals else None


# ------------------------------------------------------------------ 5. the CRM note, retried until it lands
def _queue_crm_note(
    conn: psycopg.Connection, call_id: int, vapi_call_id: str, facts: CallFacts, outcome: str, summary: str
) -> None:
    patient = conn.execute(
        "SELECT patient_id FROM bookings WHERE source_call_id = %s ORDER BY id LIMIT 1", (vapi_call_id,)
    ).fetchone()
    if patient is None:  # no booking on this call: find the patient by a phone number the caller gave a tool
        for tool in facts.tools:
            phone = to_e164(str(tool.args.get("phone", ""))) if tool.args.get("phone") else None
            found = (
                conn.execute("SELECT id FROM patients WHERE phone = %s", (phone,)).fetchone()
                if phone
                else None
            )
            if found:
                patient = found
                break
    if patient is None:
        return  # anonymous caller (an FAQ, a transfer): nothing to attach a note to
    exists = conn.execute("SELECT 1 FROM crm_outbox WHERE call_id = %s", (call_id,)).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO crm_outbox (call_id, patient_id, note) VALUES (%s, %s, %s)",
            (call_id, patient[0], f"AI receptionist call ({outcome}): {summary}"),
        )


def process_crm_outbox(conn: psycopg.Connection, crm: CrmPort, max_attempts: int = 5) -> dict[str, int]:
    """Deliver pending notes. A CRM that is down leaves them pending (with the attempt counted), never lost."""
    counts = {"done": 0, "retry": 0, "failed": 0}
    rows = conn.execute(
        "SELECT o.id, o.note, o.attempts, p.id, p.phone, p.name, p.crm_contact_id FROM crm_outbox o"
        " JOIN patients p ON p.id = o.patient_id WHERE o.status = 'pending' ORDER BY o.id"
    ).fetchall()
    for out_id, note, attempts, pid, phone, name, contact in rows:
        try:
            contact = contact or crm.upsert_contact({"phone": phone, "name": name})
            crm.add_call_note(contact, note)
        except AdapterUnavailable as exc:
            final = attempts + 1 >= max_attempts
            conn.execute(
                "UPDATE crm_outbox SET attempts = attempts + 1, last_error = %s, status = %s WHERE id = %s",
                (str(exc), "failed" if final else "pending", out_id),
            )
            counts["failed" if final else "retry"] += 1
        else:
            conn.execute("UPDATE patients SET crm_contact_id = %s WHERE id = %s", (contact, pid))
            conn.execute(
                "UPDATE crm_outbox SET status = 'done', attempts = attempts + 1 WHERE id = %s", (out_id,)
            )
            counts["done"] += 1
    return counts


def process_pending(
    conn: psycopg.Connection, crm: CrmPort | None = None, complete_fn: Callable[..., Any] | None = None
) -> dict[str, int]:
    """The worker's one pass: analyse every pending call, then deliver the CRM notes."""
    ids = [
        r[0]
        for r in conn.execute("SELECT vapi_call_id FROM calls WHERE analysis_status = 'pending' ORDER BY id")
    ]
    result = {"analysed": 0, "needs_review": 0}
    for vapi_id in ids:
        status = process_call(conn, vapi_id, complete_fn)
        conn.commit()
        result["analysed" if status == "done" else "needs_review"] += 1
    result.update({f"crm_{k}": v for k, v in process_crm_outbox(conn, crm or FakeCrm()).items()})
    conn.commit()
    return result


def main() -> int:
    """`make worker`: one pass over everything pending. (A cron or loop can call this repeatedly.)"""
    with psycopg.connect(get_settings().database_url) as conn:
        print(json.dumps(process_pending(conn)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
