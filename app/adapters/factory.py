"""Pick the calendar and CRM from settings: the fake by default, the real one when asked and configured.

Real WRITES still obey DRY_RUN (default true): with it on, the real adapters read but only LOG what they would write.
"""

from zoneinfo import ZoneInfo

import psycopg

from app.adapters.fakes import FakeCalendar, FakeCrm
from app.adapters.google_calendar import GoogleCalendar, service_account_token_fn
from app.adapters.hubspot import HubSpotCrm
from app.adapters.ports import CalendarPort, CrmPort
from app.settings import Settings


def make_calendar(conn: psycopg.Connection, settings: Settings) -> CalendarPort:
    if settings.calendar_backend == "fake":
        return FakeCalendar(conn)
    if settings.calendar_backend == "google":
        if not (settings.google_calendar_id and settings.google_credentials_file):
            raise ValueError(
                "CALENDAR_BACKEND=google needs GOOGLE_CALENDAR_ID and GOOGLE_CREDENTIALS_FILE in .env"
            )
        return GoogleCalendar(
            settings.google_calendar_id,
            service_account_token_fn(settings.google_credentials_file),
            ZoneInfo(settings.clinic_timezone),
            dry_run=settings.dry_run,
        )
    raise ValueError(f"unknown CALENDAR_BACKEND {settings.calendar_backend!r} (fake | google)")


def make_crm(settings: Settings) -> CrmPort:
    if settings.crm_backend == "fake":
        return FakeCrm()
    if settings.crm_backend == "hubspot":
        token = settings.hubspot_token.get_secret_value()
        if not token:
            raise ValueError("CRM_BACKEND=hubspot needs HUBSPOT_TOKEN in .env")
        return HubSpotCrm(token, dry_run=settings.dry_run)
    raise ValueError(f"unknown CRM_BACKEND {settings.crm_backend!r} (fake | hubspot)")
