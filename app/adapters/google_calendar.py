# Adapted from P03 (p03-ai-operations-agent/app/adapters/google_calendar.py): provider ids, P11 event marker.
"""Google Calendar adapter (service account, one shared test calendar).

Reading busy time is always allowed. Creating or deleting events is a REAL write, so with dry_run=True it only logs
and returns a `dryrun-` id. Events we create carry our booking id in a private property: that is how
`busy_intervals` tells OUR events (already counted from Postgres) from other people's events (which block time).
Events created by another portfolio project on the same calendar (P03's `p03_booking_id`) are not dental
appointments and are ignored, so two projects can share one test calendar without blocking each other's slots.
"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.adapters.http import call
from app.adapters.ports import AdapterUnavailable
from app.adapters.resilience import CircuitBreaker, retry

logger = logging.getLogger(__name__)
API = "https://www.googleapis.com/calendar/v3/calendars"
SCOPE = "https://www.googleapis.com/auth/calendar"
OUR_MARKER = "p11_booking_id"
FOREIGN_MARKERS = ("p03_booking_id",)  # other projects' events on the shared calendar: not our business


def service_account_token_fn(credentials_file: str) -> Callable[[], str]:
    """Builds a function returning a fresh OAuth token from the service-account JSON file."""

    def token() -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=[SCOPE])
        if not creds.valid:
            try:
                creds.refresh(Request())
            except Exception as exc:  # network or auth failure: never print the library's message
                raise AdapterUnavailable(f"google auth: {type(exc).__name__}") from None
        return str(creds.token)

    return token


def _parse(edge: dict[str, str], tz: ZoneInfo) -> datetime:
    if "dateTime" in edge:
        return datetime.fromisoformat(edge["dateTime"].replace("Z", "+00:00"))
    d = date.fromisoformat(edge["date"])  # all-day event: spans local midnight to midnight
    return datetime.combine(d, time.min, tzinfo=tz).astimezone(UTC)


class GoogleCalendar:
    def __init__(
        self,
        calendar_id: str,
        token_fn: Callable[[], str],
        tz: ZoneInfo,
        dry_run: bool = True,
        client: httpx.Client | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.url = f"{API}/{calendar_id}/events"
        self.token_fn, self.tz, self.dry_run = token_fn, tz, dry_run
        self.client = client or httpx.Client()
        self.breaker = breaker or CircuitBreaker("google_calendar")
        self._retry_kw: dict[str, Any] = {"sleep": sleep} if sleep else {}

    def _req(self, method: str, url: str, safe_to_retry: bool, **kw: Any) -> httpx.Response:
        def once() -> httpx.Response:
            headers = {"Authorization": f"Bearer {self.token_fn()}"}
            return call(self.client, method, url, name="google_calendar", headers=headers, **kw)

        def guarded() -> httpx.Response:
            return self.breaker.call(once)

        return retry(guarded, **self._retry_kw) if safe_to_retry else guarded()

    def busy_intervals(
        self, provider_id: int, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        resp = self._req(
            "GET", self.url, True, ok=(200,),
            params={"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "maxResults": 250},
        )  # fmt: skip
        out = []
        for ev in resp.json().get("items", []):
            if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
                continue  # cancelled, or marked "free"
            private = ev.get("extendedProperties", {}).get("private", {})
            if OUR_MARKER in private or any(m in private for m in FOREIGN_MARKERS):
                continue  # ours (Postgres knows it) or another project's event (not a dental booking)
            out.append((_parse(ev["start"], self.tz), _parse(ev["end"], self.tz)))
        return out

    def create_event(
        self, booking_id: int, provider_id: int, start: datetime, end: datetime, title: str
    ) -> str:
        # Deterministic id (a-v and 0-9 only): retrying the same booking+time cannot create a second event.
        event_id = f"p11b{booking_id}t{int(start.timestamp())}"
        if self.dry_run:
            logger.info("calendar dry-run: would create event", extra={"booking_id": booking_id})
            return f"dryrun-{event_id}"
        body = {
            "id": event_id, "summary": title,
            "start": {"dateTime": start.astimezone(UTC).isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.astimezone(UTC).isoformat(), "timeZone": "UTC"},
            "extendedProperties": {"private": {OUR_MARKER: str(booking_id), "p11_provider": str(provider_id)}},
        }  # fmt: skip
        # 409 = this id already exists = an earlier attempt succeeded: treat as done.
        self._req("POST", self.url, True, ok=(200, 201), tolerate=(409,), json=body)
        return event_id

    def delete_event(self, event_id: str) -> None:
        if self.dry_run or event_id.startswith("dryrun-"):
            return
        self._req(
            "DELETE", f"{self.url}/{event_id}", True, ok=(200, 204), tolerate=(404, 410)
        )  # already gone = fine

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        """Used only by the live check: the event, or None if it is gone."""
        resp = self._req("GET", f"{self.url}/{event_id}", True, ok=(200,), tolerate=(404, 410))
        body = resp.json() if resp.status_code == 200 else None
        return None if body is None or body.get("status") == "cancelled" else body
