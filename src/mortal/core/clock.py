"""Time helpers. Time is an integer count of minutes since the start of calendar day 1, 00:00.

A *story day* runs dawn-to-dawn (06:00 -> 06:00), so the small hours still belong to the
day that began the previous morning.
"""

from __future__ import annotations

import re

from mortal.core.constants import DAWN_MINUTE, DAY_MINUTES

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_HHMM = re.compile(r"^\s*(\d{1,2})\s*[:.hH]\s*(\d{2})\s*$")


def story_day(t: int) -> int:
    """Zero-based index of the story day containing minute `t`."""
    return (t - DAWN_MINUTE) // DAY_MINUTES


def day_start(t: int) -> int:
    """Minute of the dawn that opened the story day containing `t`."""
    return story_day(t) * DAY_MINUTES + DAWN_MINUTE


def minute_of_day(t: int) -> int:
    return t % DAY_MINUTES


def hhmm(t: int) -> str:
    m = minute_of_day(t)
    return f"{m // 60:02d}:{m % 60:02d}"


def weekday(t: int) -> str:
    return WEEKDAYS[story_day(t) % 7]


def is_weekend(t: int) -> bool:
    return story_day(t) % 7 >= 5


def is_dawn(t: int) -> bool:
    return minute_of_day(t) == DAWN_MINUTE


def phase(t: int) -> str:
    h = minute_of_day(t) // 60
    if 5 <= h < 8:
        return "dawn"
    if 8 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 21:
        return "evening"
    return "night"


def is_night(t: int) -> bool:
    return phase(t) == "night"


def parse_clock(text: str, now: int) -> int | None:
    """Resolve an "HH:MM" (or "now") string to an absolute minute inside the current story day.

    Times before dawn belong to the *next* calendar morning's small hours, so "01:30"
    resolves to the night ahead rather than the night that has already passed.
    Returns None when the text is not a time.
    """
    s = text.strip().lower()
    if s in {"now", "immediately", "right now"}:
        return now
    match = _HHMM.match(s)
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    m = hours * 60 + minutes
    start = day_start(now)
    return start + (m - DAWN_MINUTE if m >= DAWN_MINUTE else m + DAY_MINUTES - DAWN_MINUTE)
