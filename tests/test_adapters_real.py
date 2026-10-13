"""The real calendar and CRM adapters, tested against in-memory fake servers (no network, no account, no cost)."""

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.adapters.fakes import FakeCalendar, FakeCrm
from app.adapters.google_calendar import GoogleCalendar
from app.adapters.http import AdapterRejected
from app.adapters.hubspot import HubSpotCrm
from app.adapters.ports import AdapterUnavailable
from app.adapters.resilience import CircuitBreaker

TZ = ZoneInfo("America/Chicago")
START = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
END = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def cal(handler, dry_run=False, breaker=None):
    return GoogleCalendar(
        "cal@x",
        lambda: "tok",
        TZ,
        dry_run=dry_run,
        client=client(handler),
        breaker=breaker,
        sleep=lambda s: None,
    )


# ------------------------------------------------------------------ Google Calendar
def event(start, end, private=None, **extra):
    ev = {"start": {"dateTime": start}, "end": {"dateTime": end}, **extra}
    if private:
        ev["extendedProperties"] = {"private": private}
    return ev


def test_busy_time_ignores_our_events_other_projects_events_cancelled_and_free_ones():
    items = [
        event("2026-10-23T15:00:00Z", "2026-10-23T15:30:00Z"),  # someone's real meeting: blocks
        event(
            "2026-10-23T15:30:00Z", "2026-10-23T16:00:00Z", {"p11_booking_id": "5"}
        ),  # ours: Postgres knows it
        event(
            "2026-10-23T15:00:00Z", "2026-10-23T16:00:00Z", {"p03_booking_id": "9"}
        ),  # the HVAC project's job: not our business
        event("2026-10-23T15:00:00Z", "2026-10-23T16:00:00Z", status="cancelled"),
        event("2026-10-23T15:00:00Z", "2026-10-23T16:00:00Z", transparency="transparent"),
        {
            "start": {"date": "2026-10-24"},
            "end": {"date": "2026-10-25"},
        },  # all-day: local midnight to midnight
    ]
    calendar = cal(lambda req: httpx.Response(200, json={"items": items}))
    busy = calendar.busy_intervals(1, START, END)
    assert len(busy) == 2 and busy[0][0].hour == 15
    assert busy[1][0] == datetime(2026, 10, 24, 5, 0, tzinfo=UTC)  # Chicago midnight in UTC


def test_creating_an_event_sends_a_deterministic_id_and_our_marker_and_treats_409_as_already_done():
    seen = []

    def handler(req):
        seen.append((req.method, json.loads(req.content)))
        return httpx.Response(409)  # an earlier attempt already created it

    assert cal(handler).create_event(7, 3, START, END, "Cleaning: Maria") == f"p11b7t{int(START.timestamp())}"
    method, body = seen[0]
    assert (
        method == "POST"
        and body["id"].startswith("p11b7t")
        and body["extendedProperties"]["private"]["p11_booking_id"] == "7"
    )
    assert body["start"]["timeZone"] == "UTC"


def test_dry_run_never_touches_the_network():
    def handler(req):
        raise AssertionError("dry run must not call Google")

    calendar = cal(handler, dry_run=True)
    event_id = calendar.create_event(7, 3, START, END, "x")
    assert event_id.startswith("dryrun-") and calendar.delete_event(event_id) is None


def test_deleting_an_event_that_is_already_gone_is_fine():
    assert cal(lambda req: httpx.Response(404)).delete_event("p11b7t1") is None


def test_a_failing_google_becomes_adapter_unavailable_and_the_breaker_stops_hammering_it():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503)

    breaker = CircuitBreaker("g", threshold=3)
    calendar = cal(handler, breaker=breaker)
    for _ in range(3):
        with pytest.raises(AdapterUnavailable):
            calendar.busy_intervals(1, START, END)
    before = len(calls)
    with pytest.raises(AdapterUnavailable, match="temporarily disabled"):
        calendar.busy_intervals(1, START, END)
    assert len(calls) == before and breaker.state == "open"


def test_a_wrong_token_is_a_rejection_not_a_retry_loop():
    calls = []
    calendar = cal(lambda req: (calls.append(1), httpx.Response(403))[1])
    with pytest.raises(AdapterRejected):
        calendar.busy_intervals(1, START, END)
    assert len(calls) == 1


# ------------------------------------------------------------------ HubSpot
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def crm(handler, dry_run=False):
    return HubSpotCrm("tok", dry_run=dry_run, client=client(handler), sleep=lambda s: None, now=lambda: NOW)


def test_a_new_patient_is_searched_then_created_once():
    log = []

    def handler(req):
        log.append((req.method, req.url.path))
        if req.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(201, json={"id": "501"})

    assert crm(handler).upsert_contact({"phone": "+15125550142", "name": "Maria Lopez"}) == "501"
    assert log == [("POST", "/crm/v3/objects/contacts/search"), ("POST", "/crm/v3/objects/contacts")]


def test_a_stored_contact_id_is_updated_directly_and_a_deleted_one_falls_back_to_search():
    def handler(req):
        if req.method == "PATCH" and req.url.path.endswith("/gone"):
            return httpx.Response(404)
        if req.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [{"id": "77"}]})
        return httpx.Response(200, json={"id": "ok"})

    assert crm(handler).upsert_contact({"phone": "+1", "name": "A B", "crm_contact_id": "42"}) == "42"
    assert crm(handler).upsert_contact({"phone": "+1", "name": "A B", "crm_contact_id": "gone"}) == "77"


def test_a_call_note_is_created_on_the_contact_with_a_timestamp():
    seen = {}

    def handler(req):
        seen.update(path=req.url.path, body=json.loads(req.content))
        return httpx.Response(201, json={"id": "n9"})

    assert crm(handler).add_call_note("501", "AI receptionist call (booked): ...") == "n9"
    body = seen["body"]
    assert seen["path"] == "/crm/v3/objects/notes" and body["properties"]["hs_timestamp"].startswith(
        "2026-10-13T12:00"
    )
    assert (
        body["associations"][0]["to"]["id"] == "501"
        and body["associations"][0]["types"][0]["associationTypeId"] == 202
    )


def test_a_missing_scope_is_a_rejection_and_dry_run_sends_nothing():
    with pytest.raises(AdapterRejected):
        crm(lambda req: httpx.Response(403)).add_call_note("501", "n")

    def never(req):
        raise AssertionError("dry run must not call HubSpot")

    dry = crm(never, dry_run=True)
    assert (
        dry.upsert_contact({"phone": "+1", "name": "x"}).startswith("dryrun-")
        and dry.add_call_note("dryrun-contact-1", "n") == "dryrun-note"
    )


# ------------------------------------------------------------------ one contract, fake and real
@pytest.mark.parametrize(
    "make",
    [
        lambda: FakeCrm(),
        lambda: crm(lambda r: httpx.Response(200, json={"id": "1", "results": []}), dry_run=True),
    ],
)
def test_every_crm_offers_the_same_two_calls(make):
    adapter = make()
    contact = adapter.upsert_contact({"id": 1, "phone": "+15125550142", "name": "A B"})
    assert isinstance(contact, str) and isinstance(adapter.add_call_note(contact, "note"), str)


def test_the_fake_and_real_calendars_share_the_port_methods(conn):
    for adapter in (FakeCalendar(conn), cal(lambda r: httpx.Response(200, json={"items": []}), dry_run=True)):
        assert all(callable(getattr(adapter, m)) for m in ("busy_intervals", "create_event", "delete_event"))


# ------------------------------------------------------------------ the helper scripts
def test_import_p03_env_copies_values_without_printing_them(tmp_path, capsys):
    from scripts.import_p03_env import main

    src, dest = tmp_path / "p03.env", tmp_path / "p11.env"
    src.write_text(
        "GOOGLE_CALENDAR_ID=cal-secret-id   # test calendar\nGOOGLE_CREDENTIALS_FILE=/nope/key.json\nHUBSPOT_TOKEN=pat-na1-SECRET\nOTHER=1\n"
    )
    dest.write_text("DRY_RUN=true\nGOOGLE_CALENDAR_ID=\nHUBSPOT_TOKEN=\nCALENDAR_BACKEND=fake\n")
    assert main(["--from", str(src), "--to", str(dest)]) == 0
    out = capsys.readouterr().out
    assert (
        "SECRET" not in out and "cal-secret-id" not in out and "/nope" not in out
    )  # nothing sensitive displayed
    text = dest.read_text()
    assert (
        "GOOGLE_CALENDAR_ID=cal-secret-id\n" in text
        and "HUBSPOT_TOKEN=pat-na1-SECRET\n" in text
        and "GOOGLE_CREDENTIALS_FILE=/nope/key.json" in text
    )
    assert (
        "DRY_RUN=true" in text and "CALENDAR_BACKEND=fake" in text and "OTHER" not in text
    )  # only the three keys, switches untouched


def test_live_check_refuses_without_confirm_or_while_dry_run_is_on(capsys, monkeypatch):
    from app.settings import get_settings
    from scripts.live_check import main

    assert main(["calendar"]) == 2 and "--confirm" in capsys.readouterr().out
    monkeypatch.setenv("DRY_RUN", "true")
    get_settings.cache_clear()
    assert main(["hubspot", "--confirm"]) == 2 and "DRY_RUN" in capsys.readouterr().out


def test_the_calendar_round_trip_logic_creates_reads_ignores_own_event_and_deletes():
    from scripts.live_check import check_calendar

    store: dict[str, dict] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "POST":
            body = json.loads(req.content)
            store[body["id"]] = {**body, "status": "confirmed"}
            return httpx.Response(200, json=body)
        if req.method == "DELETE":
            store.pop(path.rsplit("/", 1)[1], None)
            return httpx.Response(204)
        if path.endswith("/events"):
            return httpx.Response(200, json={"items": list(store.values())})
        eid = path.rsplit("/", 1)[1]
        return httpx.Response(200, json=store[eid]) if eid in store else httpx.Response(404)

    steps = check_calendar(cal(handler), datetime(2026, 10, 13, 12, 0, tzinfo=TZ))
    assert "found" in steps[1] and "True" in steps[2] and "gone" in steps[3] and not store
