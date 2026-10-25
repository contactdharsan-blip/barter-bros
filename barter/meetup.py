"""Meetups: the earliest common 30-minute slot and a public campus spot, by yes/no questions.

The exchange never sees anyone's calendar. It walks candidate slots in time order and asks
each member "free then?"; the first slot everyone says yes to wins. Then it walks the public
spots in list order the same way. Only yes/no crosses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from swap.models import DAYS, format_clock, parse_clock

# Public ASU Tempe spots with steady foot traffic.
SPOTS = ("Memorial Union", "Hayden Library", "Noble Library", "Student Pavilion")
SLOT = 30  # minutes

_DAY_NAME = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}


@dataclass(frozen=True, order=True)
class Slot:
    day_index: int
    start: int  # minutes after midnight

    @property
    def day(self) -> str:
        return DAYS[self.day_index]

    def label(self) -> str:
        return f"{_DAY_NAME[self.day]} {format_clock(self.start)}-{format_clock(self.start + SLOT)}"


@dataclass(frozen=True)
class Block:
    """A stretch of time a student is free (private to their agent)."""

    day: str
    start: int
    end: int

    def covers(self, slot: Slot) -> bool:
        return slot.day == self.day and self.start <= slot.start and slot.start + SLOT <= self.end

    @classmethod
    def parse(cls, text: str) -> Block:
        """'SA 10:00 AM-12:00 PM'"""
        day, span = text.split(" ", 1)
        start, end = span.split("-")
        return cls(day, parse_clock(start), parse_clock(end))


@dataclass(frozen=True)
class Window:
    """The public window the exchange proposes meetups in, e.g. this weekend 10 AM-4 PM."""

    days: tuple[str, ...]
    start: int
    end: int

    def slots(self) -> list[Slot]:
        out = []
        for day in sorted(self.days, key=DAYS.index):
            for start in range(self.start, self.end - SLOT + 1, SLOT):
                out.append(Slot(DAYS.index(day), start))
        return out

    def label(self) -> str:
        days = "/".join(_DAY_NAME[d] for d in sorted(self.days, key=DAYS.index))
        return f"{days} {format_clock(self.start)}-{format_clock(self.end)}"


@dataclass(frozen=True)
class Meetup:
    slot: Slot
    spot: str

    def label(self) -> str:
        return f"{self.slot.label()} at {self.spot}"


Ask = Callable[[str, object], bool]  # (handle, slot or spot) -> yes/no


def negotiate(handles: list[str], window: Window, free: Ask, spot_ok: Ask,
              spots: tuple[str, ...] = SPOTS) -> tuple[Meetup | None, str]:
    """(meetup, "") or (None, reason). Each question stops at the first no."""
    slot = next((s for s in window.slots() if all(free(h, s) for h in handles)), None)
    if slot is None:
        return None, f"no 30-minute slot in {window.label()} works for all {len(handles)} of you"
    spot = next((p for p in spots if all(spot_ok(h, p) for h in handles)), None)
    if spot is None:
        return None, f"no public spot on the list ({', '.join(spots)}) works for all {len(handles)} of you"
    return Meetup(slot, spot), ""


# ---------------------------------------------------------------- free times, from what a student says

_DAY_WORDS = {"mon": "MO", "monday": "MO", "tue": "TU", "tues": "TU", "tuesday": "TU", "wed": "WE",
              "wednesday": "WE", "thu": "TH", "thur": "TH", "thurs": "TH", "thursday": "TH", "fri": "FR",
              "friday": "FR", "sat": "SA", "saturday": "SA", "sun": "SU", "sunday": "SU"}
_DAY = r"(?:" + "|".join(sorted(_DAY_WORDS, key=len, reverse=True)) + r")s?|weekends?"
# "free", "available" or "can meet", then a day within a few words: "only free sunday afternoon",
# "free sat 1-3pm", "available saturday or sunday after 2". "Free couch" names no day.
_AVAILABLE = re.compile(rf"\b(?:free|available|around|can meet)\b[^.;!?]{{0,25}}?\b(?:{_DAY})\b")
_DAYS_SAID = re.compile(rf"\b({_DAY})\b")
_PART = {"morning": (8 * 60, 12 * 60), "afternoon": (12 * 60, 17 * 60), "evening": (17 * 60, 21 * 60)}
_T = r"(\d{1,2})(?::(\d\d))?\s*(am|pm)?"
_RANGE = re.compile(rf"\b{_T}\s*(?:-|–|to|until|till)\s*{_T}")
_AFTER = re.compile(rf"\b(?:after|from)\s+{_T}")
_BEFORE = re.compile(rf"\b(?:before|until|till)\s+{_T}")
ALL_DAY = (0, 24 * 60)


def _clock(hour: str, minute: str | None, half: str | None) -> int:
    h = int(hour) % 12 + (12 if half == "pm" else 0)
    if half is None and 1 <= int(hour) <= 7:  # "after 2" means 2 PM: nobody meets at 2 AM
        h += 12
    return h * 60 + int(minute or 0)


def parse_availability(text: str) -> tuple[list[Block], str] | None:
    """('I'm only free sunday afternoon') -> ([Block('SU', 12:00, 17:00)], 'Sun 12:00 PM-5:00 PM'),
    or None when the text says nothing about when the student is free. Private to their agent."""
    low = re.sub(r"\b(?:noon|midday)\b", "12pm", text.lower())
    if not _AVAILABLE.search(low):
        return None
    days: list[str] = []
    for word in _DAYS_SAID.findall(low):
        word = word.rstrip("s") if word not in ("tues", "thurs") else word
        for day in (("SA", "SU") if word.startswith("weekend") else (_DAY_WORDS.get(word) or _DAY_WORDS[word + "s"],)):
            if day not in days:
                days.append(day)
    if m := _RANGE.search(low):
        h1, m1, a1, h2, m2, a2 = m.groups()
        end = _clock(h2, m2, a2)
        start = _clock(h1, m1, a1 or a2)
        if start >= end and a1 is None:  # "11-1pm" is 11 AM to 1 PM
            start = _clock(h1, m1, "am")
        span = (start, end)
        when = f"{format_clock(start)}-{format_clock(end)}"
    elif m := _AFTER.search(low):
        span = (_clock(*m.groups()), ALL_DAY[1])
        when = f"after {format_clock(span[0])}"
    elif m := _BEFORE.search(low):
        span = (ALL_DAY[0], _clock(*m.groups()))
        when = f"before {format_clock(span[1])}"
    else:
        part = next((k for k in _PART if k in low), None)
        span = _PART[part] if part else ALL_DAY
        when = f"{part} ({format_clock(span[0])}-{format_clock(span[1])})" if part else "any time"
    if span[0] >= span[1]:
        return None
    days.sort(key=DAYS.index)
    return [Block(d, *span) for d in days], f"{'/'.join(_DAY_NAME[d] for d in days)} {when}"
