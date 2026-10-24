"""Plain data: meetings, sections and what a student wants."""

from __future__ import annotations

from dataclasses import dataclass, field

DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def parse_clock(text: str) -> int:
    """'10:10 AM' -> minutes after midnight (610). Also accepts 24-hour '13:30'."""
    text = text.strip().upper()
    suffix = None
    if text.endswith(("AM", "PM")):
        text, suffix = text[:-2].strip(), text[-2:]
    hours, minutes = (int(part) for part in text.split(":"))
    if suffix == "AM" and hours == 12:
        hours = 0
    elif suffix == "PM" and hours != 12:
        hours += 12
    return hours * 60 + minutes


def format_clock(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    suffix = "AM" if hours < 12 else "PM"
    return f"{(hours - 1) % 12 + 1}:{mins:02d} {suffix}"


@dataclass(frozen=True)
class Meeting:
    days: frozenset[str]
    start: int  # minutes after midnight
    end: int

    def clashes(self, other: Meeting) -> bool:
        return bool(self.days & other.days) and self.start < other.end and other.start < self.end

    def __str__(self) -> str:
        days = " ".join(d.title() for d in DAYS if d in self.days)
        return f"{days} {format_clock(self.start)}-{format_clock(self.end)}"


@dataclass(frozen=True)
class Section:
    class_nbr: str
    course: str  # "CHM 113"
    section: str  # "1001"
    component: str  # LEC / LAB / REC
    meetings: tuple[Meeting, ...]
    seats_taken: int
    capacity: int
    campus: str = ""
    instructor: str | None = None

    @property
    def has_open_seat(self) -> bool:
        return self.seats_taken < self.capacity

    def clashes(self, other: Section) -> bool:
        return any(a.clashes(b) for a in self.meetings for b in other.meetings)

    def when(self) -> str:
        return ", ".join(str(m) for m in self.meetings) or "online, no set time"

    def label(self) -> str:
        return f"{self.course} #{self.class_nbr} ({self.when()})"


@dataclass(frozen=True)
class Want:
    """A section of `course` the student would take instead of the one they hold.

    Either a list of acceptable class numbers, or a rule. A rule never matches a
    section with no meetings (online), because it is a statement about times.
    """

    course: str
    sections: frozenset[str] | None = None
    days_in: frozenset[str] | None = None  # every meeting day must be in this set
    start_at_or_after: int | None = None
    end_by: int | None = None

    def matches(self, section: Section) -> bool:
        if section.course != self.course:
            return False
        if self.sections is not None:
            return section.class_nbr in self.sections
        if not section.meetings:
            return False
        for m in section.meetings:
            if self.days_in is not None and not m.days <= self.days_in:
                return False
            if self.start_at_or_after is not None and m.start < self.start_at_or_after:
                return False
            if self.end_by is not None and m.end > self.end_by:
                return False
        return True


@dataclass(frozen=True)
class TimePrefs:
    """Standing preferences that apply to every want, e.g. 'nothing before 10 AM'."""

    not_before: int | None = None
    not_after: int | None = None
    days_off: frozenset[str] = field(default_factory=frozenset)

    def conflict(self, section: Section) -> str | None:
        """Why this section breaks a preference, or None if it doesn't."""
        for m in section.meetings:
            if self.not_before is not None and m.start < self.not_before:
                return f"starts before {format_clock(self.not_before)}, and you said nothing earlier"
            if self.not_after is not None and m.end > self.not_after:
                return f"ends after {format_clock(self.not_after)}, and you said nothing later"
            if m.days & self.days_off:
                return "meets on a day you keep free"
        return None

    def allows(self, section: Section) -> bool:
        return self.conflict(section) is None
