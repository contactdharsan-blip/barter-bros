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
# Read clause by clause, so a time binds to its own day: "busy saturday, free sunday after 2".
# A clause about the student's own times names a day and says free ("free", "available",
# "around", "can meet", "can do", "works") or not ("not free", "can't do", "busy", "no").
# A question or a clause about "you" is about someone else and is left alone.
_FREE = re.compile(r"\b(?:free|available|around|can meet|can do|works?)\b")
# A negation counts only when it's about meeting. In a clause that says "free", it has to be close
# before that word ("I won't be available saturday", "saturday isn't free for me", "not really
# free"); elsewhere in the clause it's about something else ("free saturday, can't wait", "I don't
# have class saturday so I'm free"). Without "free", it has to be about doing or making the day
# ("can't do saturday", "saturday doesn't work", "not saturday"), or say busy/bad/no good.
_NEG = r"(?:\b(?:not|cant|cannot|wont|dont|aint|never)\b|n't\b)"
_NOT_FREE = re.compile(rf"{_NEG}(?:\s+\w+){{0,3}}?\s+(?:free|available|around|works?)\b")
_NOT_DAY = re.compile(
    rf"{_NEG}\s+(?:\w+\s+)?(?:do|make|meet|come|works?|(?:on\s+)?(?:{_DAY}))\b"
    rf"|\b(?:busy|unavailable|bad|except)\b|\bno\s+(?:good|go\b|(?:{_DAY})\b)"
)
_NOT_ME = re.compile(r"\bnever\s*mind\b|\bi\s+(?:don't|dont|do not)\s+work\b")  # "I don't work saturday" = free


def _not(clause: str) -> bool:
    clause = _NOT_ME.sub(" ", clause)
    return bool((_NOT_FREE if _FREE.search(clause) else _NOT_DAY).search(clause)
                or re.search(r"\bexcept\s+(?:on\s+)?(?:" + _DAY + r")\b", clause))


_ASKS = re.compile(r"\byou\b|\byour\b|^(?:are|is|do|does|would|could|will|how|what|when)\b")
_CLAUSE = re.compile(r"(.*?)([,;.!?]+|\s+but\s+|$)")
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


@dataclass(frozen=True)
class Times:
    """What a student said about their own times. Private to their agent."""

    free: list[Block]  # when they said they're free
    busy: tuple[str, ...]  # days they said they can't do
    said: str  # read back to them: "you can't do Sat; you're free Sun after 2:00 PM"
    other: bool  # the message also says something else (a question, a name, news) worth relaying


def _days(clause: str) -> list[str]:
    days: list[str] = []
    for word in _DAYS_SAID.findall(clause):
        word = word.rstrip("s") if word not in ("tues", "thurs") else word
        for day in (("SA", "SU") if word.startswith("weekend") else (_DAY_WORDS.get(word) or _DAY_WORDS[word + "s"],)):
            if day not in days:
                days.append(day)
    return sorted(days, key=DAYS.index)


def _span(clause: str) -> tuple[tuple[int, int], str]:
    """The stretch of a day one clause names, and how to read it back."""
    if m := _RANGE.search(clause):
        h1, m1, a1, h2, m2, a2 = m.groups()
        end = _clock(h2, m2, a2)
        start = _clock(h1, m1, a1 or a2)
        if start >= end and a1 is None:  # "11-1pm" is 11 AM to 1 PM
            start = _clock(h1, m1, "am")
        return (start, end), f"{format_clock(start)}-{format_clock(end)}"
    if m := _AFTER.search(clause):
        start = _clock(*m.groups())
        return (start, ALL_DAY[1]), f"after {format_clock(start)}"
    if m := _BEFORE.search(clause):
        end = _clock(*m.groups())
        return (ALL_DAY[0], end), f"before {format_clock(end)}"
    part = next((k for k in _PART if k in clause), None)
    if part:
        return _PART[part], f"{part} ({format_clock(_PART[part][0])}-{format_clock(_PART[part][1])})"
    return ALL_DAY, "any time"


def parse_availability(text: str) -> Times | None:
    """("I'm only free sunday afternoon") -> Times(free=[Block('SU', 12:00, 17:00)], busy=(), ...),
    or None when the text says nothing about when the student is or isn't free."""
    low = re.sub(r"\b(?:noon|midday)\b", "12pm", text.lower().replace("\u2019", "'"))
    free: list[Block] = []
    busy: list[str] = []
    said: list[str] = []
    other = False
    for m in _CLAUSE.finditer(low):
        clause = m.group(1).strip()
        if not clause:
            continue
        days = _days(clause)
        asks = "?" in m.group(2) or _ASKS.search(clause)
        if not days or asks or not (_FREE.search(clause) or _not(clause)):
            other = True
            continue
        if _not(clause):
            busy += [d for d in days if d not in busy]
            said.append("you can't do " + "/".join(_DAY_NAME[d] for d in days))
            continue
        span, when = _span(clause)
        if span[0] >= span[1]:
            other = True
            continue
        free += [Block(d, *span) for d in days]
        said.append(f"you're free {'/'.join(_DAY_NAME[d] for d in days)} {when}")
    if not free and not busy:
        return None
    free = [b for b in free if b.day not in busy]
    return Times(free, tuple(busy), "; ".join(said), other)
