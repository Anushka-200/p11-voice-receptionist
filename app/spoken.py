"""Turn dates, times and phone numbers into words a voice can read naturally.

Analogy: a TTS voice is a reader with no context. "2:00" or "512-555-0142" come out odd, so the server
hands the model ready-to-say text and the agent just repeats it.
"""

from datetime import date, datetime

DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
ORDINALS = [
    "", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
    "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
    "eighteenth", "nineteenth", "twentieth", "twenty-first", "twenty-second", "twenty-third",
    "twenty-fourth", "twenty-fifth", "twenty-sixth", "twenty-seventh", "twenty-eighth", "twenty-ninth",
    "thirtieth", "thirty-first",
]  # fmt: skip
HOUR_WORDS = [
    "twelve",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
]
MINUTE_WORDS = {0: "", 15: "fifteen", 30: "thirty", 45: "forty-five"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = [
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December",
]  # fmt: skip


def spoken_time(dt: datetime) -> str:
    """9:00 -> 'nine AM', 14:30 -> 'two thirty PM'. Only quarter-hour times are used by the clinic grid."""
    hour = HOUR_WORDS[dt.hour % 12]
    minute = MINUTE_WORDS.get(dt.minute, str(dt.minute))
    return f"{hour} {minute} {'AM' if dt.hour < 12 else 'PM'}".replace("  ", " ")


def spoken_date(dt: date) -> str:
    return f"{WEEKDAYS[dt.weekday()]}, {MONTHS[dt.month - 1]} {ORDINALS[dt.day]}"


def spoken_slot(dt: datetime) -> str:
    """'Thursday, October fifteenth at two PM' (matches the eval scenarios)."""
    return f"{spoken_date(dt)} at {spoken_time(dt)}"


def spoken_phone(e164: str) -> str:
    """+15125550142 -> 'five one two, five five five, zero one four two' (US numbers, country code dropped)."""
    d = e164[-10:]
    words = [DIGITS[int(c)] for c in d]
    return f"{' '.join(words[:3])}, {' '.join(words[3:6])}, {' '.join(words[6:])}"
