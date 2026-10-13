"""The six tools against the real Postgres: rules, ownership, idempotency, outages and a real two-caller race."""

import json
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest

from app.adapters.fakes import FakeCalendar, OutboxMessaging
from app.availability import parse_slot_id
from app.settings import get_settings
from app.tools import ToolContext, execute

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=TZ)  # Wednesday, same clock as the eval scenarios
PHONE = "five one two, five five five, zero one four two"
E164 = "+15125550142"


def make_ctx(conn) -> ToolContext:
    return ToolContext(conn, FakeCalendar(conn), OutboxMessaging(conn), get_settings(), now=lambda: NOW)


@pytest.fixture
def ctx(conn):
    return make_ctx(conn)


def run(ctx, call_id, tool, **args):
    return execute(ctx, call_id, tool, args, commit=False)


def offer(ctx, call_id="c1", when="Thursday afternoon", kind="checkup"):
    r = run(ctx, call_id, "check_availability", when_text=when, appointment_type=kind)
    assert r["ok"], r
    return r


def book(ctx, call_id="c1", slot=None, name="Maria Lopez", phone=PHONE):
    slot = slot or offer(ctx, call_id)["slots"][0]["slot_id"]
    return run(ctx, call_id, "book_appointment", slot_id=slot, name=name, phone=phone, reason="checkup")


# ------------------------------------------------------------------ availability
def test_offers_at_most_two_spread_out_slots_with_ready_to_say_text(ctx):
    r = offer(ctx)
    assert len(r["slots"]) == 2
    assert (
        r["say"]
        == "I have Thursday, October fifteenth at twelve PM or Thursday, October fifteenth at two PM."
    )


def test_next_friday_asks_which_one_and_offers_nothing(ctx):
    r = run(ctx, "c1", "check_availability", when_text="next Friday", appointment_type="checkup")
    assert r["needs_clarification"] and "slots" not in r
    assert "October sixteenth" in r["say"] and "October twenty-third" in r["say"]


def test_a_closed_day_offers_the_next_openings_instead(ctx):
    r = run(ctx, "c1", "check_availability", when_text="Saturday", appointment_type="checkup")
    assert r["is_alternative"] and r["say"].startswith("That day is full")


def test_a_calendar_block_removes_the_slot(ctx):
    for provider in (1, 2):  # both dentists busy 12:00 to 13:00 on Thursday
        ctx.conn.execute(
            "INSERT INTO calendar_events (provider_id, start_at, end_at, title) VALUES (%s, %s, %s, 'busy')",
            (provider, datetime(2026, 10, 15, 12, 0, tzinfo=TZ), datetime(2026, 10, 15, 13, 0, tzinfo=TZ)),
        )
    assert "twelve PM" not in offer(ctx)["say"]


def test_nothing_is_offered_sooner_than_an_hour_from_now(ctx):
    r = run(ctx, "c1", "check_availability", when_text="today", appointment_type="checkup")
    starts = [parse_slot_id(s["slot_id"], TZ)[1] for s in r["slots"]]
    assert starts and all(
        t >= NOW + timedelta(hours=1) for t in starts
    )  # clock is 10:00, so 11:00 is the earliest


def test_unknown_type_and_unclear_day_are_recoverable_errors(ctx):
    assert (
        run(ctx, "c1", "check_availability", when_text="Thursday", appointment_type="x")["error"]
        == "unknown_type"
    )
    assert (
        run(ctx, "c1", "check_availability", when_text="whenever", appointment_type="checkup")["error"]
        == "unclear_day"
    )


# ------------------------------------------------------------------ booking rules
def test_booking_creates_row_calendar_event_and_sms_and_reads_back(ctx):
    r = book(ctx)
    assert r["ok"] and "You're booked for a checkup on Thursday, October fifteenth at twelve PM" in r["say"]
    n = lambda sql: ctx.conn.execute(sql).fetchone()[0]  # noqa: E731
    assert n("SELECT count(*) FROM bookings WHERE source_call_id = 'c1'") == 1
    assert n("SELECT count(*) FROM calendar_events WHERE booking_id IS NOT NULL") >= 1
    assert n("SELECT count(*) FROM outbound_messages WHERE idempotency_key LIKE 'confirm:%'") >= 1


def test_the_agent_can_only_book_a_slot_this_call_was_offered(ctx):
    offer(ctx)
    r = run(
        ctx, "c1", "book_appointment", slot_id="slot_20261015_1000_p1", name="A B", phone=PHONE, reason="x"
    )
    assert r["error"] == "slot_not_offered"
    other_call = run(
        ctx,
        "c2",
        "book_appointment",
        slot_id=offer(ctx)["slots"][0]["slot_id"],
        name="A B",
        phone=PHONE,
        reason="x",
    )
    assert other_call["error"] == "slot_not_offered"  # c2 was never offered it


@pytest.mark.parametrize("heard", ["nine seven two five five five zero one", "", "tomorrow"])
def test_partial_phone_is_refused_with_a_spoken_reask_and_no_booking(ctx, heard):
    r = book(ctx, phone=heard)
    assert r["error"] == "bad_phone" and "whole number" in r["say"]
    assert ctx.conn.execute("SELECT count(*) FROM bookings WHERE source_call_id = 'c1'").fetchone()[0] == 0


def test_same_request_twice_makes_one_booking(ctx):
    slot = offer(ctx)["slots"][0]["slot_id"]
    first = book(ctx, slot=slot)
    again = book(ctx, slot=slot)
    assert first["booking_id"] == again["booking_id"] and again["replayed"] is True
    assert ctx.conn.execute("SELECT count(*) FROM bookings WHERE source_call_id = 'c1'").fetchone()[0] == 1


def test_a_slot_taken_by_someone_else_is_refused_politely(ctx):
    slot = offer(ctx)["slots"][0]["slot_id"]
    pid = ctx.conn.execute(
        "INSERT INTO patients (phone, name) VALUES ('+19995550001', 'Other') RETURNING id"
    ).fetchone()[0]
    ctx.conn.execute(  # another caller got there first (both dentists, so whichever provider the slot named)
        "INSERT INTO bookings (patient_id, provider_id, type_code, start_at, end_at)"
        " SELECT %s, id, 'checkup', %s, %s FROM providers WHERE role = 'dentist'",
        (pid, datetime(2026, 10, 15, 12, 0, tzinfo=TZ), datetime(2026, 10, 15, 12, 30, tzinfo=TZ)),
    )
    r = book(ctx, slot=slot)
    assert r["error"] == "slot_taken" and "just taken" in r["say"]


def test_calendar_outage_is_spoken_as_a_transfer_offer_and_leaves_no_booking(ctx):
    slot = offer(ctx)["slots"][0]["slot_id"]
    ctx.calendar.fail = True
    r = book(ctx, slot=slot)
    assert r["error"] == "calendar_unavailable" and r["suggest_transfer"] is True
    assert "front desk" in r["say"] and "Traceback" not in json.dumps(r)
    assert ctx.conn.execute("SELECT count(*) FROM bookings WHERE source_call_id = 'c1'").fetchone()[0] == 0


# ------------------------------------------------------------------ reschedule / cancel / find: ownership
def make_existing(ctx, phone=E164):
    """A booking made on an EARLIER call (call id 'old')."""
    book(ctx, call_id="old", phone=phone)
    return ctx.conn.execute("SELECT id FROM bookings WHERE source_call_id = 'old'").fetchone()[0]


def test_cannot_cancel_or_move_a_booking_not_looked_up_by_phone_on_this_call(ctx):
    bid = make_existing(ctx)
    assert run(ctx, "new", "cancel_appointment", booking_id=bid)["error"] == "no_such_booking"
    offer(ctx, "new")
    assert (
        run(ctx, "new", "reschedule_appointment", booking_id=bid, new_slot_id="slot_20261015_1400_p1")[
            "error"
        ]
        == "no_such_booking"
    )
    assert ctx.conn.execute("SELECT status FROM bookings WHERE id = %s", (bid,)).fetchone()[0] == "booked"


def test_find_then_cancel_works_and_a_wrong_phone_finds_nothing(ctx):
    bid = make_existing(ctx)
    assert (
        run(ctx, "x", "find_my_appointment", phone="nine seven two five five five zero one three one")[
            "bookings"
        ]
        == []
    )
    found = run(ctx, "new", "find_my_appointment", phone=PHONE)
    assert found["bookings"][0]["booking_id"] == bid and "Thursday, October fifteenth" in found["say"]
    assert run(ctx, "new", "cancel_appointment", booking_id=bid)["ok"]
    assert ctx.conn.execute("SELECT status FROM bookings WHERE id = %s", (bid,)).fetchone()[0] == "cancelled"
    assert (
        run(ctx, "new", "cancel_appointment", booking_id=bid)["error"] == "no_such_booking"
    )  # already cancelled


def test_find_then_reschedule_moves_the_booking_and_calendar_event(ctx):
    bid = make_existing(ctx)
    run(ctx, "new", "find_my_appointment", phone=PHONE)
    new_slot = offer(ctx, "new", when="Friday morning")["slots"][0]["slot_id"]
    assert run(ctx, "new", "reschedule_appointment", booking_id=bid, new_slot_id=new_slot)["ok"]
    start = (
        ctx.conn.execute("SELECT start_at FROM bookings WHERE id = %s", (bid,)).fetchone()[0].astimezone(TZ)
    )
    assert (start.day, start.hour) == (16, 8)
    assert (
        ctx.conn.execute("SELECT count(*) FROM calendar_events WHERE booking_id = %s", (bid,)).fetchone()[0]
        == 1
    )


def test_reschedule_needs_an_offered_slot_of_the_same_type(ctx):
    bid = make_existing(ctx)
    run(ctx, "new", "find_my_appointment", phone=PHONE)
    cleaning = offer(ctx, "new", kind="cleaning")["slots"][0][
        "slot_id"
    ]  # offered for a cleaning, booking is a checkup
    assert (
        run(ctx, "new", "reschedule_appointment", booking_id=bid, new_slot_id=cleaning)["error"]
        == "slot_not_offered"
    )


# ------------------------------------------------------------------ faq, validation, unknown tools
def test_faq_answers_from_the_table_and_offers_a_human_when_it_does_not_know(ctx):
    assert "eight to five" in run(ctx, "c", "faq_lookup", question="what are your opening hours")["say"]
    assert "Delta Dental" in run(ctx, "c", "faq_lookup", question="do you take Delta Dental insurance")["say"]
    unknown = run(ctx, "c", "faq_lookup", question="do you sell cars")
    assert unknown["suggest_transfer"] is True and unknown["topic"] is None


def test_bad_arguments_and_unknown_tools_never_raise(ctx):
    assert run(ctx, "c", "book_appointment", slot_id="x")["error"] == "bad_arguments"
    assert run(ctx, "c", "drop_all_tables")["error"] == "unknown_tool"


def test_every_call_is_audited_even_failures_with_phones_hashed(ctx):
    book(ctx, phone="bad")
    row = ctx.conn.execute(
        "SELECT tool, ok, args_redacted::text FROM tool_invocations WHERE vapi_call_id = 'c1' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == "book_appointment" and row[1] is False


# ------------------------------------------------------------------ the real race: two callers, two connections
def test_two_callers_racing_for_the_same_slot_make_exactly_one_booking():
    settings = get_settings()
    try:
        conns = [psycopg.connect(settings.database_url, connect_timeout=10) for _ in range(2)]
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running (make up)")
    cleanup = psycopg.connect(settings.database_url)
    ctxs = [make_ctx(c) for c in conns]
    slots = []
    try:
        for i, c in enumerate(ctxs):  # both callers are offered the same first slot
            r = execute(
                c,
                f"t-race-{i}",
                "check_availability",
                {"when_text": "Thursday afternoon", "appointment_type": "checkup"},
            )
            slots.append(r["slots"][0]["slot_id"])
        assert slots[0] == slots[1]
        # Make both dentists' slot contested: only provider of this slot id matters, the DB constraint decides.
        barrier = threading.Barrier(2)
        results: list[dict] = [{}, {}]

        def go(i: int) -> None:
            barrier.wait()
            results[i] = execute(ctxs[i], f"t-race-{i}", "book_appointment",
                                 {"slot_id": slots[i], "name": f"Racer {i}", "phone": f"+1999555010{i}", "reason": "x"})  # fmt: skip

        threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        outcomes = sorted(r.get("error", "ok") for r in results)
        assert outcomes == ["ok", "slot_taken"], results
        n = cleanup.execute("SELECT count(*) FROM bookings WHERE source_call_id LIKE 't-race-%'").fetchone()[
            0
        ]
        assert n == 1
    finally:  # leave the dev database as we found it
        for c in conns:
            c.rollback()
            c.close()
        cleanup.rollback()
        for sql in (
            "DELETE FROM outbound_messages WHERE patient_id IN (SELECT id FROM patients WHERE phone LIKE '+1999555010%')",
            "DELETE FROM calendar_events WHERE booking_id IN (SELECT id FROM bookings WHERE source_call_id LIKE 't-race-%')",
            "DELETE FROM bookings WHERE source_call_id LIKE 't-race-%'",
            "DELETE FROM patients WHERE phone LIKE '+1999555010%'",
            "DELETE FROM idempotency_keys WHERE created_at > now() - interval '5 minutes' AND tool = 'book_appointment'",
            "DELETE FROM tool_invocations WHERE vapi_call_id LIKE 't-race-%'",
            "DELETE FROM call_sessions WHERE vapi_call_id LIKE 't-race-%'",
        ):
            cleanup.execute(sql)
        cleanup.commit()
        cleanup.close()
