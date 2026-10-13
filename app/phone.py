"""Parse a phone number as heard by the speech-to-text into +1XXXXXXXXXX, or refuse.

Speech-to-text gives numbers as digits ("512 555 0142"), as words ("five one two ..."), or a mix.
We accept those, and refuse anything that is not exactly ten US digits, so the agent re-asks instead of
booking under a wrong number (a partial number is the most common voice error).
"""

import re

from app.spoken import DIGITS

_WORD_TO_DIGIT = {w: str(i) for i, w in enumerate(DIGITS)} | {"oh": "0", "o": "0"}


def to_e164(text: str) -> str | None:
    tokens = re.findall(r"[a-zA-Z]+|\d+", text.lower())
    digits = "".join(_WORD_TO_DIGIT.get(t, t if t.isdigit() else "") for t in tokens)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"+1{digits}" if len(digits) == 10 else None
