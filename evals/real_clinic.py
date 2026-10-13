"""The eval backend that uses the REAL tools on the real database, rolled back after every scenario.

Why: the stub clinic proved the grader. This one proves the agent works with the code we actually ship
(date resolver, availability, idempotency, ownership), so a bug the stub could not show (for example the FAQ
missing "closing") surfaces here. Nothing is ever committed: each scenario ends with a rollback.

`transfer_call` and `end_call` are Vapi built-ins (not our server's), so this class records them itself.
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from app.adapters.fakes import FakeCalendar, OutboxMessaging
from app.settings import get_settings
from app.tools import ToolContext, execute
from evals.scenario_schema import Scenario

TZ = ZoneInfo("America/Chicago")
EVAL_NOW = datetime(2026, 10, 14, 10, 0, tzinfo=TZ)  # Wednesday, the clock every scenario is written for
DESTINATIONS = {"on_call", "front_desk"}


class RealClinic:
    def __init__(self, scenario: Scenario, conn: psycopg.Connection | None = None) -> None:
        settings = get_settings()
        self.conn = conn or psycopg.connect(settings.database_url, connect_timeout=10)
        self.conn.execute("SELECT 1")  # open a transaction so every tool call is a savepoint inside it
        self.ctx = ToolContext(
            self.conn, FakeCalendar(self.conn), OutboxMessaging(self.conn), settings, now=lambda: EVAL_NOW
        )
        self.call_id = f"eval-{scenario.id}"
        self.transferred_to: str | None = None
        self.ended = False
        self.faq_topics: list[str] = []
        self.rescheduled = 0
        self._seed(scenario)

    def _seed(self, scenario: Scenario) -> None:
        eb = scenario.setup.existing_booking
        if not (eb and scenario.setup.patient_phone):
            return
        patient = self.conn.execute(
            "INSERT INTO patients (phone, name) VALUES (%s, 'Existing Patient') ON CONFLICT (phone) DO UPDATE"
            " SET phone = EXCLUDED.phone RETURNING id",
            (scenario.setup.patient_phone,),
        ).fetchone()
        assert patient is not None
        # The fixed id 7 is explicit. The app role may not touch sequences (least privilege), and no seeded
        # scenario creates another booking, so nextval can never collide with it.
        start = datetime(2026, 10, 16, 9, 0, tzinfo=TZ)
        self.conn.execute(
            "INSERT INTO bookings (id, patient_id, provider_id, type_code, start_at, end_at, source_call_id)"
            " VALUES (%s, %s, 1, %s, %s, %s + interval '30 minutes', 'seed')",
            (eb["id"], patient[0], eb["type"], start, start),
        )

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "transfer_call":
            if args.get("destination") not in DESTINATIONS:
                return {
                    "ok": False,
                    "error": "bad_destination",
                    "message": "destination must be on_call or front_desk",
                }
            self.transferred_to = args["destination"]
            self.ended = True
            return {"ok": True}
        if name == "end_call":
            self.ended = True
            return {"ok": True}
        result = execute(self.ctx, self.call_id, name, args, commit=False)
        if result.get("ok") and name == "faq_lookup" and result.get("topic"):
            self.faq_topics.append(result["topic"])
        if result.get("ok") and name == "reschedule_appointment":
            self.rescheduled += 1
        return result

    def final_state(self) -> dict[str, Any]:
        ours = "(source_call_id = %s OR source_call_id = 'seed')"

        def count(status: str) -> int:
            row = self.conn.execute(
                f"SELECT count(*) FROM bookings WHERE status = %s AND {ours}", (status, self.call_id)
            ).fetchone()
            assert row is not None
            return row[0]

        last = self.conn.execute(
            f"SELECT type_code FROM bookings WHERE status = 'booked' AND {ours} ORDER BY id DESC LIMIT 1",
            (self.call_id,),
        ).fetchone()
        phone = self.conn.execute(
            "SELECT p.phone FROM bookings b JOIN patients p ON p.id = b.patient_id"
            f" WHERE b.status = 'booked' AND {ours.replace('source_call_id', 'b.source_call_id')} ORDER BY b.id DESC LIMIT 1",
            (self.call_id,),
        ).fetchone()
        return {
            "bookings_active": count("booked"),
            "booking_type": last[0] if last else None,
            "booked_phone": phone[0] if phone else None,
            "cancelled": count("cancelled"),
            "rescheduled": self.rescheduled,
            "transferred_to": self.transferred_to,
            "faq_topics": sorted(set(self.faq_topics)),
        }

    def close(self) -> None:
        self.conn.rollback()  # nothing from an eval run is ever kept
        self.conn.close()
