"""Turn what the caller said ("next Friday afternoon") into real dates, deterministically.

Analogy: a calendar clerk who never guesses. If a phrase could mean two different days, the clerk hands
back both and the agent must ask; the model is never trusted to do date arithmetic.
Rules for "next <weekday>": if that weekday is still ahead in the current Mon-Sun week it is AMBIGUOUS
(this week's or next week's), otherwise it is the upcoming one.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.spoken import MONTHS, ORDINALS, WEEKDAYS

_WEEKDAY_IDX = {w.lower(): i for i, w in enumerate(WEEKDAYS)}
_MONTH_IDX = {m.lower(): i + 1 for i, m in enumerate(MONTHS)} | {
    m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)
}
_ORDINAL_IDX = {w: i for i, w in enumerate(ORDINALS) if w}


@dataclass
class Resolution:
    dates: list[date] = field(default_factory=list)
    part_of_day: str | None = None  # morning | afternoon
    ambiguous: bool = False  # more than one candidate date: ask the caller


def _part_of_day(t: str) -> str | None:
    if "morning" in t:
        return "morning"
    if "afternoon" in t or "evening" in t or "after lunch" in t:
        return "afternoon"
    return None


def _day_of_month(t: str) -> int | None:
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m and 1 <= int(m.group(1)) <= 31:
        return int(m.group(1))
    for word in sorted(_ORDINAL_IDX, key=len, reverse=True):  # longest first: "twenty-third" before "third"
        pattern = r"[- ]".join(
            re.escape(part) for part in word.split("-")
        )  # 'twenty-third' or 'twenty third'
        if re.search(rf"\b{pattern}\b", t):
            return _ORDINAL_IDX[word]
    return None


def _next_with_day(today: date, day: int, month: int | None) -> date | None:
    """The soonest date on or after today with this day of month (and month, if given)."""
    for offset in range(0, 366):
        d = today + timedelta(days=offset)
        if d.day == day and (month is None or d.month == month):
            return d
    return None


def resolve(text: str, now: datetime) -> Resolution | None:
    """Return the candidate dates for a phrase, or None if we cannot tell (the agent then asks for a day)."""
    t = text.lower().strip()
    today = now.date()
    pod = _part_of_day(t)

    if "day after tomorrow" in t:
        return Resolution([today + timedelta(days=2)], pod)
    if "tomorrow" in t:
        return Resolution([today + timedelta(days=1)], pod)
    if re.search(r"\btoday\b|\bthis (morning|afternoon|evening)\b", t):
        return Resolution([today], pod)

    for name, idx in _WEEKDAY_IDX.items():
        if re.search(rf"\b{name}\b", t):
            ahead = (idx - today.weekday()) % 7 or 7  # 1..7 days: the upcoming one, never today
            upcoming = today + timedelta(days=ahead)
            in_this_week = upcoming.isocalendar().week == today.isocalendar().week
            if re.search(rf"\bnext {name}\b", t):
                if in_this_week:  # "next Friday" said on Wednesday: this Friday or the one after?
                    return Resolution([upcoming, upcoming + timedelta(days=7)], pod, ambiguous=True)
                return Resolution([upcoming], pod)
            return Resolution([upcoming], pod)

    for name, month in _MONTH_IDX.items():
        if re.search(rf"\b{name}\b", t):
            day = _day_of_month(t.replace(name, " "))
            found = _next_with_day(today, day, month) if day else None
            return Resolution([found], pod) if found else None

    if "next week" in t:
        monday = today + timedelta(days=7 - today.weekday())
        return Resolution([monday + timedelta(days=i) for i in range(5)], pod)
    if "this week" in t or "any day" in t or "soonest" in t or "earliest" in t or "as soon" in t:
        return Resolution([today + timedelta(days=i) for i in range(1, 8)], pod)

    day = _day_of_month(t)
    found = _next_with_day(today, day, None) if day else None
    return Resolution([found], pod) if found else None
