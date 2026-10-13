"""`make demo`: the whole system end to end, offline and free, in about ten seconds.

What it does (all against your local Postgres, cleaned up afterwards):
  1. calls the tool server over HTTP exactly as Vapi does: check availability, book, repeat the booking (replay)
  2. delivers a recorded REAL end-of-call report to /vapi/events, twice (the replay stores nothing)
  3. runs the post-call analysis (a canned model reply; pass --live-llm to use the real model, about $0.0002)
  4. shows the stored call, the CRM note, and the analytics line

Usage: uv run python -m scripts.demo [--live-llm] [--keep]
"""

import argparse
import json
import uuid
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeCrm
from app.analytics import summary
from app.api.main import create_app
from app.postcall import process_call, process_crm_outbox
from app.settings import get_settings

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "vapi" / "report_booking.json"
PHONE_WORDS, PHONE = "nine nine nine five five five zero one seven seven", "+19995550177"
CANNED = json.dumps({
    "summary": "The caller booked a teeth cleaning. The agent read the phone number and the date back and the caller confirmed.",
    "outcome": "booked", "sentiment": "positive", "phone_read_back": True, "date_read_back": True, "task_completed": True,
})  # fmt: skip


def step(title: str) -> None:
    print(f"\n=== {title}")


def tool(client: TestClient, secret: str, call_id: str, name: str, args: dict) -> dict:
    body = {
        "message": {
            "call": {"id": call_id},
            "toolCallList": [{"id": "d", "type": "function", "function": {"name": name, "arguments": args}}],
        }
    }
    r = client.post("/vapi/tools", json=body, headers={"X-Vapi-Secret": secret})
    return json.loads(r.json()["results"][0]["result"])


def cleanup(conn: psycopg.Connection, call_id: str) -> None:
    for sql in (
        "DELETE FROM outbound_messages WHERE patient_id IN (SELECT id FROM patients WHERE phone = %(p)s)",
        "DELETE FROM calendar_events WHERE booking_id IN (SELECT id FROM bookings WHERE source_call_id = %(c)s)",
        "DELETE FROM crm_outbox WHERE call_id IN (SELECT id FROM calls WHERE vapi_call_id = %(c)s)",
        "DELETE FROM bookings WHERE source_call_id = %(c)s",
        "DELETE FROM patients WHERE phone = %(p)s",
        "DELETE FROM calls WHERE vapi_call_id = %(c)s",
        "DELETE FROM tool_invocations WHERE vapi_call_id = %(c)s",
        "DELETE FROM call_sessions WHERE vapi_call_id = %(c)s",
        "DELETE FROM idempotency_keys WHERE created_at > now() - interval '10 minutes' AND tool = 'book_appointment' AND result_json::text LIKE '%%demo%%'",
    ):
        conn.execute(sql, {"c": call_id, "p": PHONE})
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="use the real model for the analysis (needs ANTHROPIC_API_KEY)",
    )
    parser.add_argument("--keep", action="store_true", help="leave the demo rows in the database")
    args = parser.parse_args(argv)
    settings = get_settings()
    secret = settings.vapi_webhook_secret.get_secret_value()
    call_id = f"demo-{uuid.uuid4().hex[:8]}"
    client = TestClient(
        create_app(analyze=lambda conn, vid: None)
    )  # the demo runs the analysis itself, visibly
    try:
        step("1. A caller books a cleaning (the same tool calls Vapi makes during a call)")
        asked = tool(
            client,
            secret,
            call_id,
            "check_availability",
            {"when_text": "next Monday morning", "appointment_type": "cleaning"},
        )
        print("check_availability ->", asked["say"])
        slot = asked["slots"][0]["slot_id"]
        args_ = {"slot_id": slot, "name": "Demo Patient", "phone": PHONE_WORDS, "reason": "cleaning"}
        booked = tool(client, secret, call_id, "book_appointment", args_)
        print("book_appointment   ->", booked["say"])
        again = tool(client, secret, call_id, "book_appointment", args_)
        print(
            f"same request again -> replayed={again.get('replayed')}, same booking id: {again['booking_id'] == booked['booking_id']}"
        )
        print(
            "a phone with only eight digits ->",
            tool(
                client,
                secret,
                call_id,
                "find_my_appointment",
                {"phone": "nine nine nine five five five zero one"},
            )["say"],
        )

        step("2. Vapi delivers the end-of-call report (a recorded real one), twice")
        report = json.loads(FIXTURE.read_text())
        report["message"]["call"]["id"] = call_id
        for attempt in (1, 2):
            r = client.post("/vapi/events", json=report, headers={"X-Vapi-Secret": secret}).json()
            print(f"delivery {attempt}: stored={r['stored']}")

        step(
            "3. Post-call analysis ("
            + ("real model" if args.live_llm else "canned model reply, offline")
            + ")"
        )
        fake = lambda messages, **kw: type("R", (), {"text": CANNED, "cost_usd": 0.0})()  # noqa: E731
        with psycopg.connect(settings.database_url) as conn:
            status = process_call(conn, call_id, None if args.live_llm else fake)
            conn.commit()
            outcome, summ, flags, scores, cost = conn.execute(
                "SELECT outcome, summary, flags, eval_scores, cost_breakdown FROM calls WHERE vapi_call_id = %s",
                (call_id,),
            ).fetchone()
            print(f"status={status} outcome={outcome} flags={flags}")
            print("summary:", summ)
            print(
                f"online checks: disclosure={scores['disclosure_said']} phone_read_back={scores.get('phone_read_back')} date_read_back={scores.get('date_read_back')}"
            )
            print(
                f"Vapi cost ${cost['total']} (stt {cost['stt']}, llm {cost['llm']}, tts {cost['tts']}, vapi {cost['vapi']}); our analysis ${cost['analysis_usd']}"
            )

            step("4. CRM note (fake CRM) and call analytics")
            crm = FakeCrm()
            print(
                "crm:",
                process_crm_outbox(conn, crm),
                "->",
                crm.notes[0][1][:90] if crm.notes else "(no note)",
            )
            conn.commit()
            s = summary(conn, "p11-receptionist")
            print(
                f"all stored receptionist calls: {s['calls']} calls, p50 turn latency {s['latency_ms']['p50']} ms, ${s['cost_per_minute'].get('total')} per minute"
            )
    finally:
        if not args.keep:
            with psycopg.connect(settings.database_url) as conn:
                cleanup(conn, call_id)
            print("\n(demo rows removed; use --keep to leave them)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
