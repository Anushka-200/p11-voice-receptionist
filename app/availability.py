# Adapted from P03 (p03-ai-operations-agent/app/availability.py): technicians -> providers, clinic hours.
"""Which appointment slots are free, and which two to offer.

Analogy: the appointment book. A slot is free when the provider has no booking and no calendar block
over it, it falls inside clinic hours, and it is not too soon to be useful.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.adapters.resilience import retry
from app.spoken import spoken_slot

# weekday (Mon=0) -> (opens, closes); weekends are closed
HOURS: dict[int, tuple[int, int]] = {0: (8, 17), 1: (8, 17), 2: (8, 17), 3: (8, 17), 4: (8, 13)}
GRID_MIN = 30  # a new appointment can start every 30 minutes
MIN_LEAD = timedelta(hours=1)  # nothing is offered sooner than an hour from now
SEARCH_AHEAD_DAYS = 14  # if the asked-for day is full, look this far for the next openings


@dataclass(frozen=True)
class Slot:
    provider_id: int
    start: datetime
    end: datetime
    type_code: str

    @property
    def slot_id(self) -> str:
        return make_slot_id(self.provider_id, self.start)

    @property
    def say(self) -> str:
        return spoken_slot(self.start)


def make_slot_id(provider_id: int, start: datetime) -> str:
    return f"slot_{start:%Y%m%d_%H%M}_p{provider_id}"  # local clinic time


def parse_slot_id(slot_id: str, tz: ZoneInfo) -> tuple[int, datetime]:
    _, day, hhmm, prov = slot_id.split("_")
    start = datetime.strptime(day + hhmm, "%Y%m%d%H%M").replace(tzinfo=tz)
    return int(prov[1:]), start


def is_free(
    ctx, provider_id: int, start: datetime, end: datetime, exclude_booking_id: int | None = None
) -> bool:
    row = ctx.conn.execute(
        "SELECT 1 FROM bookings WHERE provider_id = %s AND status = 'booked' AND start_at < %s AND end_at > %s"
        " AND id IS DISTINCT FROM %s",
        (provider_id, end, start, exclude_booking_id),
    ).fetchone()
    if row:
        return False
    # Reads are safe to repeat, so retry briefly; the breaker stops hammering a calendar that is down.
    busy = ctx.breaker.call(
        lambda: retry(lambda: ctx.calendar.busy_intervals(provider_id, start, end), attempts=2, base_s=0.1)
    )
    return not busy


def available_slots(ctx, type_code: str, day: date, part_of_day: str | None = None) -> list[Slot]:
    tz = ZoneInfo(ctx.settings.clinic_timezone)
    hours = HOURS.get(day.weekday())
    if hours is None:
        return []
    row = ctx.conn.execute(
        "SELECT duration_min, provider_role FROM appointment_types WHERE code = %s", (type_code,)
    ).fetchone()
    assert row is not None
    duration, role = timedelta(minutes=row[0]), row[1]
    providers = [
        r[0] for r in ctx.conn.execute("SELECT id FROM providers WHERE role = %s ORDER BY id", (role,))
    ]
    open_at = datetime.combine(day, time(hours[0]), tz)
    close_at = datetime.combine(day, time(hours[1]), tz)
    earliest = ctx.now() + MIN_LEAD
    slots: list[Slot] = []
    start = open_at
    while start + duration <= close_at:
        in_part = part_of_day is None or (start.hour < 12) == (part_of_day == "morning")
        if in_part and start >= earliest:
            for provider_id in providers:
                if is_free(ctx, provider_id, start, start + duration):
                    slots.append(Slot(provider_id, start, start + duration, type_code))
                    break  # one provider per start time is enough for offering
        start += timedelta(minutes=GRID_MIN)
    return slots


def choose_offers(slots: list[Slot], n: int = 2, spacing: timedelta = timedelta(hours=2)) -> list[Slot]:
    """At most n slots, spread out (a caller can't compare ten times by ear)."""
    offers: list[Slot] = []
    for slot in sorted(slots, key=lambda s: s.start):
        if not offers or slot.start - offers[-1].start >= spacing:
            offers.append(slot)
        if len(offers) == n:
            break
    return offers
