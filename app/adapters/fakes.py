# Adapted from P03 (p03-ai-operations-agent/app/adapters/fakes.py): technician -> provider, SMS outbox only.
"""Stand-ins for the calendar and SMS, used by tests, evals and dry-run demos.

Each has a `fail` switch so tests can simulate an outage (the calendar being down).
"""

from datetime import datetime

import psycopg

from app.adapters.ports import AdapterUnavailable


class FakeCalendar:
    """The calendar lives in the calendar_events table, so state survives and tests can inspect it."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise AdapterUnavailable("fake calendar is down")

    def busy_intervals(self, provider_id: int, start: datetime, end: datetime):
        self._check()
        rows = self.conn.execute(
            "SELECT start_at, end_at FROM calendar_events"
            " WHERE provider_id = %s AND booking_id IS NULL AND start_at < %s AND end_at > %s",
            (provider_id, end, start),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def create_event(
        self, booking_id: int, provider_id: int, start: datetime, end: datetime, title: str
    ) -> str:
        self._check()
        row = self.conn.execute(
            "INSERT INTO calendar_events (provider_id, start_at, end_at, title, booking_id)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (provider_id, start, end, title, booking_id),
        ).fetchone()
        assert row is not None
        return str(row[0])

    def delete_event(self, event_id: str) -> None:
        self._check()
        self.conn.execute("DELETE FROM calendar_events WHERE id = %s", (int(event_id),))


class OutboxMessaging:
    """SMS goes to the outbox table and is never delivered (DRY_RUN). Same idempotency key = one row."""

    def __init__(self, conn: psycopg.Connection, dry_run: bool = True) -> None:
        if not dry_run:
            raise NotImplementedError("v1 sends no real SMS: the outbox is dry-run only")
        self.conn = conn

    def send(self, *, patient_id: int | None, body: str, idempotency_key: str) -> int | None:
        row = self.conn.execute(
            "INSERT INTO outbound_messages (patient_id, body, idempotency_key) VALUES (%s, %s, %s)"
            " ON CONFLICT (idempotency_key) DO NOTHING RETURNING id",
            (patient_id, body, idempotency_key),
        ).fetchone()
        return row[0] if row else None


class FakeCrm:
    """A CRM in memory: contacts and call notes, with a `fail` switch to simulate an outage (HubSpot is not built)."""

    def __init__(self) -> None:
        self.contacts: dict[str, dict] = {}
        self.notes: list[tuple[str, str]] = []
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise AdapterUnavailable("fake CRM is down")

    def upsert_contact(self, patient: dict) -> str:
        self._check()
        contact_id = f"fake-{patient['phone']}"  # the same phone always maps to the same contact
        self.contacts[contact_id] = patient
        return contact_id

    def add_call_note(self, crm_contact_id: str, note: str) -> str:
        self._check()
        self.notes.append((crm_contact_id, note))
        return f"note-{len(self.notes)}"
