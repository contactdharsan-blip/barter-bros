"""Meetups: the earliest common 30-minute slot and a public campus spot, by yes/no questions.

The exchange never sees anyone's calendar. It walks candidate slots in time order and asks
each member "free then?"; the first slot everyone says yes to wins. Then it walks the public
spots in list order the same way. Only yes/no crosses.
"""

from __future__ import annotations

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
