# Adapted from P03 (p03-ai-operations-agent/app/adapters/ports.py): technician -> provider (dentist/hygienist).
"""The interfaces our tools talk to. A fake and a real adapter implement the same one.

Why: tests and evals must run on fakes (no cost, no real messages), and swapping Google Calendar
in later must not change a single tool. Contract tests run against both.
"""

from datetime import datetime
from typing import Any, Protocol


class AdapterUnavailable(RuntimeError):
    """The external system is down or timing out. Tools turn this into a recoverable error."""


class CalendarPort(Protocol):
    def busy_intervals(
        self, provider_id: int, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Busy blocks that are NOT our own bookings (our bookings are read from Postgres)."""

    def create_event(
        self, booking_id: int, provider_id: int, start: datetime, end: datetime, title: str
    ) -> str: ...

    def delete_event(self, event_id: str) -> None: ...


class CrmPort(Protocol):
    def upsert_contact(self, patient: dict[str, Any]) -> str: ...

    def add_call_note(self, crm_contact_id: str, note: str) -> str:
        """Attach the post-call summary to the contact. Returns the note id."""


class MessagingPort(Protocol):
    def send(self, *, patient_id: int | None, body: str, idempotency_key: str) -> int | None:
        """Queue an SMS. The same idempotency_key twice sends it once."""
