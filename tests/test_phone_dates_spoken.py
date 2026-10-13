from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.dates import resolve
from app.phone import to_e164
from app.spoken import spoken_phone, spoken_slot, spoken_time

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=TZ)  # a Wednesday


@pytest.mark.parametrize(
    ("heard", "expected"),
    [
        ("five one two, five five five, zero one four two", "+15125550142"),
        ("512-555-0142", "+15125550142"),
        ("+1 (512) 555 0142", "+15125550142"),
        ("one five one two five five five oh one four two", "+15125550142"),
        ("five one two five five five 0142", "+15125550142"),
    ],
)
def test_phone_formats_heard_by_speech_to_text(heard, expected):
    assert to_e164(heard) == expected


@pytest.mark.parametrize(
    "heard", ["nine seven two five five five zero one", "", "call me tomorrow", "5125550142123"]
)
def test_partial_or_wrong_phone_numbers_are_refused(heard):
    assert to_e164(heard) is None


def test_spoken_forms_match_what_the_eval_scenarios_expect():
    assert spoken_phone("+15125550142") == "five one two, five five five, zero one four two"
    assert spoken_slot(datetime(2026, 10, 15, 14, 0)) == "Thursday, October fifteenth at two PM"
    assert spoken_slot(datetime(2026, 10, 23, 9, 30)) == "Friday, October twenty-third at nine thirty AM"
    assert spoken_time(datetime(2026, 10, 15, 12, 0)) == "twelve PM"


def test_next_friday_said_on_a_wednesday_is_ambiguous_and_returns_both_dates():
    r = resolve("next Friday", NOW)
    assert r and r.ambiguous and [d.isoformat() for d in r.dates] == ["2026-10-16", "2026-10-23"]


def test_this_friday_and_bare_friday_are_not_ambiguous():
    for phrase in ("this Friday", "Friday", "on friday please"):
        r = resolve(phrase, NOW)
        assert r and not r.ambiguous and [d.isoformat() for d in r.dates] == ["2026-10-16"]


def test_next_weekday_that_already_passed_this_week_is_just_the_upcoming_one():
    r = resolve("next Monday", NOW)  # Monday is behind us this week, so it is the coming Monday
    assert r and not r.ambiguous and r.dates[0].isoformat() == "2026-10-19"


@pytest.mark.parametrize(
    ("phrase", "day", "part"),
    [
        ("tomorrow morning", "2026-10-15", "morning"),
        ("Thursday afternoon", "2026-10-15", "afternoon"),
        ("the twenty-third", "2026-10-23", None),
        ("October 23rd", "2026-10-23", None),
        ("the 15th", "2026-10-15", None),
        ("day after tomorrow", "2026-10-16", None),
    ],
)
def test_phrases_resolve_to_the_right_day_and_part_of_day(phrase, day, part):
    r = resolve(phrase, NOW)
    assert r and r.dates[0].isoformat() == day and r.part_of_day == part


def test_nonsense_returns_none_so_the_agent_asks():
    assert resolve("whenever you like", NOW) is None
