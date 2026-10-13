"""The six tools the voice agent can call, and the one function that runs them safely.

Analogy: the clinic's back office. The phone agent can ask for anything, but the back office decides what is
allowed (valid phone, only offered slots, only your own appointments), remembers what it already did
(idempotency), and never lets an error reach the caller as raw text.

Every tool returns {"ok": bool, "say": "<text the agent can read aloud>", ...}. A failure is a RETURN VALUE with
a spoken explanation and, when sensible, `suggest_transfer`: the agent never reads an exception aloud.
"""

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.fakes import FakeCalendar, OutboxMessaging
from app.adapters.ports import AdapterUnavailable, CalendarPort, MessagingPort
from app.adapters.resilience import CircuitBreaker
from app.availability import available_slots, choose_offers, is_free, make_slot_id, parse_slot_id
from app.dates import resolve
from app.phone import to_e164
from app.privacy import redact_any
from app.settings import Settings, get_settings
from app.spoken import spoken_date, spoken_slot

logger = logging.getLogger(__name__)
TOOL_BUDGET_S = 2.0  # a tool slower than this is logged as slow; Vapi plays a filler while it runs
TRANSFER_HINT = "I'm having trouble with that. Let me connect you with the front desk."


class ToolError(Exception):
    """A refusal the agent can recover from. `say` is what it should tell the caller."""

    def __init__(self, code: str, say: str, **extra: Any) -> None:
        super().__init__(code)
        self.code, self.say, self.extra = code, say, extra


@dataclass
class ToolContext:
    conn: psycopg.Connection
    calendar: CalendarPort
    messaging: MessagingPort
    settings: Settings
    now: Callable[[], datetime]
    breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("calendar"))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.settings.clinic_timezone)


def make_context(conn: psycopg.Connection, now: Callable[[], datetime] | None = None) -> ToolContext:
    settings = get_settings()
    if settings.calendar_backend != "fake":
        raise NotImplementedError("the Google Calendar adapter is a separate task (Phase 2b)")
    tz = ZoneInfo(settings.clinic_timezone)
    return ToolContext(
        conn=conn,
        calendar=FakeCalendar(conn),
        messaging=OutboxMessaging(conn, dry_run=True),
        settings=settings,
        now=now or (lambda: datetime.now(tz)),
    )


# ------------------------------------------------------------------ call session + idempotency
def _session(ctx: ToolContext, call_id: str) -> dict[str, Any]:
    ctx.conn.execute(
        "INSERT INTO call_sessions (vapi_call_id) VALUES (%s) ON CONFLICT DO NOTHING", (call_id,)
    )
    row = ctx.conn.execute(
        "SELECT offered_slots, found_booking_ids FROM call_sessions WHERE vapi_call_id = %s FOR UPDATE",
        (call_id,),
    ).fetchone()
    assert row is not None
    return {"offered": row[0], "found": row[1]}


def _save_session(
    ctx: ToolContext, call_id: str, offered: list | None = None, found: list | None = None
) -> None:
    if offered is not None:
        ctx.conn.execute(
            "UPDATE call_sessions SET offered_slots = %s WHERE vapi_call_id = %s",
            (json.dumps(offered), call_id),
        )
    if found is not None:
        ctx.conn.execute(
            "UPDATE call_sessions SET found_booking_ids = %s WHERE vapi_call_id = %s",
            (json.dumps(found), call_id),
        )


def _idem_key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def _idem_get(ctx: ToolContext, key: str) -> dict[str, Any] | None:
    row = ctx.conn.execute("SELECT result_json FROM idempotency_keys WHERE key = %s", (key,)).fetchone()
    return row[0] if row else None


def _idem_put(ctx: ToolContext, key: str, tool: str, result: dict[str, Any]) -> None:
    ctx.conn.execute(
        "INSERT INTO idempotency_keys (key, tool, result_json) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (key, tool, json.dumps(result)),
    )


# ------------------------------------------------------------------ helpers
def _type(ctx: ToolContext, code: str) -> dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT code, name, duration_min, spoken_name FROM appointment_types WHERE code = %s", (code,)
    ).fetchone()
    if row is None:
        raise ToolError(
            "unknown_type",
            "I can book a cleaning, a checkup, a filling or a whitening consultation. Which one?",
        )
    return {"code": row[0], "name": row[1], "minutes": row[2], "spoken": row[3]}


def _phone(raw: str) -> str:
    phone = to_e164(raw)
    if phone is None:
        raise ToolError(
            "bad_phone", "I didn't catch a full ten digit number. Could you say the whole number again?"
        )
    return phone


def _confirm_sms(ctx: ToolContext, patient_id: int, key: str, text: str) -> None:
    ctx.messaging.send(patient_id=patient_id, body=f"Bright Smile Dental: {text}", idempotency_key=key)


# ------------------------------------------------------------------ the tools
class CheckArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    when_text: str = Field(
        description="The caller's own words for the day, e.g. 'next Friday afternoon' or 'October 23rd'"
    )
    appointment_type: str = Field(description="One of: cleaning, checkup, filling, whitening, emergency")


def check_availability(ctx: ToolContext, call_id: str, a: CheckArgs) -> dict[str, Any]:
    t = _type(ctx, a.appointment_type)
    res = resolve(a.when_text, ctx.now())
    if res is None:
        raise ToolError(
            "unclear_day", "Which day would you like? For example, Thursday, or October twenty-third."
        )
    if res.ambiguous:  # never guess between two dates: hand both back and make the agent ask
        first, second = res.dates
        say = f"Do you mean this {spoken_date(first)}, or next week's {spoken_date(second)}?"
        return {
            "ok": True,
            "needs_clarification": True,
            "say": say,
            "candidates": [d.isoformat() for d in res.dates],
        }

    found: list = []
    for day in res.dates:
        found += available_slots(ctx, t["code"], day, res.part_of_day)
    offers = choose_offers(found)
    alternative = False
    if not offers:  # that day (or part of day) is full: offer the next openings instead
        alternative = True
        last = res.dates[-1]
        for i in range(1, 15):
            found += available_slots(ctx, t["code"], last.fromordinal(last.toordinal() + i))
            offers = choose_offers(found)
            if len(offers) >= 2:
                break
    if not offers:
        raise ToolError(
            "no_slots",
            "I don't see any openings in the next two weeks. Let me connect you with the front desk.",
            suggest_transfer=True,
        )

    session = _session(ctx, call_id)
    known = {o["slot_id"]: o for o in session["offered"]}  # keep earlier offers: the caller may go back
    for s in offers:
        known[s.slot_id] = {"slot_id": s.slot_id, "type": t["code"], "say": s.say}
    _save_session(ctx, call_id, offered=list(known.values()))

    spoken = " or ".join(s.say for s in offers)
    lead = "That day is full. The next openings are" if alternative else "I have"
    return {
        "ok": True,
        "say": f"{lead} {spoken}.",
        "slots": [{"slot_id": s.slot_id, "say": s.say} for s in offers],
        "is_alternative": alternative,
    }


class BookArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    slot_id: str = Field(description="A slot_id returned by check_availability on this call")
    name: str = Field(description="The caller's full name")
    phone: str = Field(
        description="The phone number exactly as the caller said it, already read back and confirmed"
    )
    reason: str = Field(default="", description="Why they are coming, in a few words")


def book_appointment(ctx: ToolContext, call_id: str, a: BookArgs) -> dict[str, Any]:
    phone = _phone(a.phone)
    name = a.name.strip()
    if not name:
        raise ToolError("bad_name", "May I have your full name for the booking?")
    session = _session(ctx, call_id)
    offered = {o["slot_id"]: o for o in session["offered"]}
    if a.slot_id not in offered:  # the model may only book what this call was actually offered
        raise ToolError("slot_not_offered", "Let me check the times again so we pick one that's open.")
    key = _idem_key("book", call_id, a.slot_id, phone)
    if (stored := _idem_get(ctx, key)) is not None:
        return {**stored, "replayed": True}

    t = _type(ctx, offered[a.slot_id]["type"])
    provider_id, start = parse_slot_id(a.slot_id, ctx.tz)
    end = start + timedelta(minutes=t["minutes"])
    if not is_free(ctx, provider_id, start, end):
        raise ToolError("slot_taken", "Sorry, that time was just taken. Let me find another one.")

    patient = ctx.conn.execute(
        "INSERT INTO patients (phone, name) VALUES (%s, %s) ON CONFLICT (phone) DO UPDATE SET phone = EXCLUDED.phone"
        " RETURNING id",
        (phone, name),
    ).fetchone()
    assert patient is not None
    try:
        with ctx.conn.transaction():  # savepoint: a DB-level refusal leaves the rest of the request intact
            booking = ctx.conn.execute(
                "INSERT INTO bookings (patient_id, provider_id, type_code, start_at, end_at, reason, source_call_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (patient[0], provider_id, t["code"], start, end, a.reason, call_id),
            ).fetchone()
    except (
        psycopg.errors.ExclusionViolation
    ):  # two calls raced for the same slot; the database said no to one
        raise ToolError("slot_taken", "Sorry, that time was just taken. Let me find another one.") from None
    assert booking is not None
    event_id = ctx.breaker.call(
        lambda: ctx.calendar.create_event(booking[0], provider_id, start, end, f"{t['name']}: {name}")
    )
    ctx.conn.execute("UPDATE bookings SET calendar_event_id = %s WHERE id = %s", (event_id, booking[0]))
    _confirm_sms(
        ctx,
        patient[0],
        f"confirm:{booking[0]}",
        f"your {t['name'].lower()} is booked for {spoken_slot(start)}.",
    )
    _save_session(ctx, call_id, found=sorted({*session["found"], booking[0]}))

    result = {
        "ok": True,
        "booking_id": booking[0],
        "say": f"You're booked for {t['spoken']} on {spoken_slot(start)}. You'll get a text confirmation.",
    }
    _idem_put(ctx, key, "book_appointment", result)
    return result


class FindArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    phone: str = Field(description="The phone number on the booking, as the caller said it")


def find_my_appointment(ctx: ToolContext, call_id: str, a: FindArgs) -> dict[str, Any]:
    phone = _phone(a.phone)
    session = _session(ctx, call_id)
    rows = ctx.conn.execute(
        "SELECT b.id, b.start_at, t.spoken_name, t.code FROM bookings b JOIN patients p ON p.id = b.patient_id"
        " JOIN appointment_types t ON t.code = b.type_code"
        " WHERE p.phone = %s AND b.status = 'booked' AND b.start_at >= %s ORDER BY b.start_at LIMIT 3",
        (phone, ctx.now()),
    ).fetchall()
    # Only bookings found by phone on THIS call can be changed or cancelled on this call.
    _save_session(ctx, call_id, found=sorted({*session["found"], *(r[0] for r in rows)}))
    if not rows:
        return {"ok": True, "bookings": [], "say": "I don't see an upcoming appointment under that number."}
    items = [{"booking_id": r[0], "type": r[3], "when": spoken_slot(r[1].astimezone(ctx.tz))} for r in rows]
    spoken = " and ".join(f"{r[2]} on {spoken_slot(r[1].astimezone(ctx.tz))}" for r in rows)
    return {"ok": True, "bookings": items, "say": f"I see {spoken}."}


def _owned_booking(ctx: ToolContext, call_id: str, booking_id: int):
    session = _session(ctx, call_id)
    row = ctx.conn.execute(
        "SELECT id, patient_id, provider_id, type_code, start_at, calendar_event_id FROM bookings"
        " WHERE id = %s AND status = 'booked'",
        (booking_id,),
    ).fetchone()
    if booking_id not in session["found"] or row is None:  # not looked up on this call, or not active
        raise ToolError(
            "no_such_booking",
            "I can't find that appointment. Let me look it up with your phone number first.",
        )
    return session, row


class RescheduleArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    booking_id: int = Field(description="A booking_id returned by find_my_appointment on this call")
    new_slot_id: str = Field(description="A slot_id returned by check_availability for the new time")


def reschedule_appointment(ctx: ToolContext, call_id: str, a: RescheduleArgs) -> dict[str, Any]:
    session, row = _owned_booking(ctx, call_id, a.booking_id)
    offered = {o["slot_id"]: o for o in session["offered"]}
    if a.new_slot_id not in offered or offered[a.new_slot_id]["type"] != row[3]:
        raise ToolError("slot_not_offered", "Let me check the times again so we pick one that's open.")
    key = _idem_key("resched", call_id, a.booking_id, a.new_slot_id)
    if (stored := _idem_get(ctx, key)) is not None:
        return {**stored, "replayed": True}

    t = _type(ctx, row[3])
    provider_id, start = parse_slot_id(a.new_slot_id, ctx.tz)
    end = start + timedelta(minutes=t["minutes"])
    if not is_free(ctx, provider_id, start, end, exclude_booking_id=a.booking_id):
        raise ToolError("slot_taken", "Sorry, that time was just taken. Let me find another one.")
    try:
        with ctx.conn.transaction():
            ctx.conn.execute(
                "UPDATE bookings SET provider_id = %s, start_at = %s, end_at = %s WHERE id = %s",
                (provider_id, start, end, a.booking_id),
            )
    except psycopg.errors.ExclusionViolation:
        raise ToolError("slot_taken", "Sorry, that time was just taken. Let me find another one.") from None
    if row[5]:
        ctx.breaker.call(lambda: ctx.calendar.delete_event(row[5]))
    event_id = ctx.breaker.call(
        lambda: ctx.calendar.create_event(a.booking_id, provider_id, start, end, t["name"])
    )
    ctx.conn.execute("UPDATE bookings SET calendar_event_id = %s WHERE id = %s", (event_id, a.booking_id))
    _confirm_sms(
        ctx,
        row[1],
        f"resched:{a.booking_id}:{a.new_slot_id}",
        f"your {t['name'].lower()} moved to {spoken_slot(start)}.",
    )
    result = {
        "ok": True,
        "say": f"Done. Your {t['spoken'].removeprefix('a ').removeprefix('an ')} is now {spoken_slot(start)}.",
    }
    _idem_put(ctx, key, "reschedule_appointment", result)
    return result


class CancelArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    booking_id: int = Field(description="A booking_id returned by find_my_appointment on this call")


def cancel_appointment(ctx: ToolContext, call_id: str, a: CancelArgs) -> dict[str, Any]:
    _, row = _owned_booking(ctx, call_id, a.booking_id)
    ctx.conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = %s", (a.booking_id,))
    if row[5]:
        ctx.breaker.call(lambda: ctx.calendar.delete_event(row[5]))
    _confirm_sms(
        ctx,
        row[1],
        f"cancel:{a.booking_id}",
        f"your appointment on {spoken_slot(row[4].astimezone(ctx.tz))} is cancelled.",
    )
    return {"ok": True, "say": "It's cancelled. Please call us whenever you want to rebook."}


class FaqArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str = Field(
        description="The caller's question in a few words, e.g. 'opening hours' or 'Delta Dental insurance'"
    )


def faq_lookup(ctx: ToolContext, call_id: str, a: FaqArgs) -> dict[str, Any]:
    q = a.question.lower()
    tokens = set(re.findall(r"[a-z']+", q))
    best, best_score = None, 0
    for topic, keywords, answer in ctx.conn.execute("SELECT topic, keywords, answer FROM faq"):
        score = sum(1 for k in keywords if (k in q if len(k) >= 4 else k in tokens))
        if score > best_score:
            best, best_score = (topic, answer), score
    if best is None:
        return {
            "ok": True,
            "topic": None,
            "say": "I don't have that information. I can connect you with the front desk.",
            "suggest_transfer": True,
        }
    return {"ok": True, "topic": best[0], "say": best[1]}


TOOLS: dict[str, tuple[type[BaseModel], Callable[..., dict[str, Any]]]] = {
    "check_availability": (CheckArgs, check_availability),
    "book_appointment": (BookArgs, book_appointment),
    "find_my_appointment": (FindArgs, find_my_appointment),
    "reschedule_appointment": (RescheduleArgs, reschedule_appointment),
    "cancel_appointment": (CancelArgs, cancel_appointment),
    "faq_lookup": (FaqArgs, faq_lookup),
}


# ------------------------------------------------------------------ the one safe entry point
def execute(
    ctx: ToolContext, call_id: str, name: str, raw_args: dict[str, Any], *, commit: bool = True
) -> dict[str, Any]:
    """Validate, run, time and log one tool call. Never raises: every failure becomes a spoken result."""
    started = time.perf_counter()
    entry = TOOLS.get(name)
    try:
        if entry is None:
            raise ToolError("unknown_tool", TRANSFER_HINT, suggest_transfer=True)
        args_model, handler = entry
        try:
            args = args_model.model_validate(raw_args)
        except ValidationError:
            raise ToolError(
                "bad_arguments", "I need a bit more information for that. Could you repeat it?"
            ) from None
        # One transaction per tool call: if the tool fails, only ITS writes are undone (earlier calls on this
        # call, such as the offered slots, stay). Nested inside a test's transaction it becomes a savepoint.
        with ctx.conn.transaction():
            result = handler(ctx, call_id, args)
    except ToolError as exc:
        result = {"ok": False, "error": exc.code, "say": exc.say, **exc.extra}
    except AdapterUnavailable:
        result = {
            "ok": False,
            "error": "calendar_unavailable",
            "say": TRANSFER_HINT,
            "suggest_transfer": True,
        }
    except Exception:
        logger.exception("tool crashed", extra={"tool": name})
        result = {"ok": False, "error": "internal_error", "say": TRANSFER_HINT, "suggest_transfer": True}

    elapsed = time.perf_counter() - started
    try:  # the audit row is written even when the tool failed
        ctx.conn.execute(
            "INSERT INTO tool_invocations (vapi_call_id, tool, args_redacted, latency_ms, ok) VALUES (%s, %s, %s, %s, %s)",
            (call_id, name, json.dumps(redact_any(raw_args)), round(elapsed * 1000), bool(result.get("ok"))),
        )
        if commit:
            ctx.conn.commit()
    except Exception:
        logger.exception("could not write the audit row")
        ctx.conn.rollback()
    logger.info(
        "tool",
        extra={
            "tool": name,
            "ok": result.get("ok"),
            "ms": round(elapsed * 1000),
            "slow": elapsed > TOOL_BUDGET_S,
        },
    )
    return result


def slot_id_for(provider_id: int, start: datetime) -> str:  # re-exported for tests
    return make_slot_id(provider_id, start)
