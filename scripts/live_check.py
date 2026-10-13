"""A REAL round trip against Google Calendar or HubSpot, with clearly labelled test data that it removes afterwards.

This writes to real accounts, so it refuses to run unless BOTH hold: you pass --confirm, and DRY_RUN=false in .env
(set it just for the check, then put it back to true). It never prints a secret.

Usage:
  uv run python -m scripts.live_check calendar --confirm
  uv run python -m scripts.live_check hubspot --confirm [--keep]
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.adapters.google_calendar import GoogleCalendar, service_account_token_fn
from app.adapters.hubspot import HubSpotCrm
from app.settings import get_settings


def check_calendar(cal: GoogleCalendar, now: datetime) -> list[str]:
    start = (now + timedelta(days=2)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )  # a quiet slot, clearly labelled
    steps = []
    event_id = cal.create_event(
        999999, 0, start, start + timedelta(minutes=30), "P11 live check (safe to delete)"
    )
    steps.append(f"created event {event_id}")
    steps.append("read it back: " + ("found" if cal.get_event(event_id) else "NOT FOUND"))
    busy = cal.busy_intervals(0, start - timedelta(hours=1), start + timedelta(hours=1))
    steps.append(
        f"our own event is correctly NOT counted as busy time: {not any(b[0] == start for b in busy)}"
    )
    cal.delete_event(event_id)
    steps.append("deleted it; read back: " + ("gone" if cal.get_event(event_id) is None else "STILL THERE"))
    return steps


def check_hubspot(crm: HubSpotCrm, keep: bool) -> list[str]:
    steps = []
    contact = crm.upsert_contact({"phone": "+19995550199", "name": "P11 Livecheck"})
    steps.append(f"contact upserted (id {contact})")
    again = crm.upsert_contact({"phone": "+19995550199", "name": "P11 Livecheck", "crm_contact_id": contact})
    steps.append(f"upserting again gave the same contact (no duplicate): {again == contact}")
    note = crm.add_call_note(contact, "P11 live check: this note is safe to delete.")
    steps.append(f"note created (id {note})")
    if not keep:
        crm.delete("notes", note)
        crm.delete("contacts", contact)
        steps.append("note and contact archived (removed)")
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=["calendar", "hubspot"])
    parser.add_argument("--confirm", action="store_true", help="yes, really write to the real account")
    parser.add_argument("--keep", action="store_true", help="hubspot: leave the test records in place")
    args = parser.parse_args(argv)
    s = get_settings()
    if not args.confirm:
        print(f"This would write to your REAL {args.target}. Add --confirm to proceed.")
        return 2
    if s.dry_run:
        print(
            "DRY_RUN is true, so the adapters would only log. For this check set DRY_RUN=false in .env, then put it back."
        )
        return 2
    try:
        if args.target == "calendar":
            if not (s.google_calendar_id and s.google_credentials_file):
                print(
                    "GOOGLE_CALENDAR_ID and GOOGLE_CREDENTIALS_FILE are not set in .env (run: python -m scripts.import_p03_env)"
                )
                return 2
            tz = ZoneInfo(s.clinic_timezone)
            cal = GoogleCalendar(
                s.google_calendar_id,
                service_account_token_fn(s.google_credentials_file),
                tz,
                dry_run=False,
                client=httpx.Client(),
            )
            steps = check_calendar(cal, datetime.now(UTC).astimezone(tz))
        else:
            token = s.hubspot_token.get_secret_value()
            if not token:
                print("HUBSPOT_TOKEN is not set in .env (run: python -m scripts.import_p03_env)")
                return 2
            steps = check_hubspot(HubSpotCrm(token, dry_run=False), args.keep)
    except Exception as exc:  # the adapters' messages carry the service and the HTTP status, never a secret
        print(f"FAILED: {type(exc).__name__}: {exc}")
        print(
            "A 403 on HubSpot notes usually means the private app needs the notes scope; a 404 on Google means the calendar is not shared with the service account."
        )
        return 1
    print("\n".join(f"  ok: {x}" for x in steps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
