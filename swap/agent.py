"""A student's personal agent.

Everything here is private to one student: their name, their whole schedule, what
they want and why. The only thing that leaves the agent is an `Offer`.
"""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, field
from typing import Callable

from .models import Section, TimePrefs, Want

OFFER_KEYS = ("handle", "course", "have", "accept")


@dataclass(frozen=True)
class Offer:
    """The whole of what an agent tells the exchange."""

    handle: str
    course: str
    have: str
    accept: tuple[str, ...]

    def payload(self) -> dict:
        return {"handle": self.handle, "course": self.course, "have": self.have, "accept": list(self.accept)}

    @classmethod
    def from_payload(cls, payload: dict) -> Offer:
        if set(payload) != set(OFFER_KEYS):
            raise ValueError(f"offer payload must have exactly {OFFER_KEYS}, got {sorted(payload)}")
        return cls(payload["handle"], payload["course"], payload["have"], tuple(payload["accept"]))


@dataclass(frozen=True)
class Skipped:
    """A section that matched the want but was ruled out, and why (told only to the student)."""

    section: Section
    reason: str


# A student's answer to a question: kind is "proposal", "anchor", "plan" or "step".
Answerer = Callable[[str, dict], str]


@dataclass
class StudentAgent:
    name: str
    schedule: list[Section]
    wants: list[Want]
    prefs: TimePrefs = field(default_factory=TimePrefs)
    reasons: dict[str, str] = field(default_factory=dict)  # course -> why; never shared
    answer: Answerer = lambda kind, context: "no"
    rng: random.Random | None = None
    handle: str = ""

    def __post_init__(self) -> None:
        if not self.handle:
            token = f"{self.rng.getrandbits(24):06x}" if self.rng else secrets.token_hex(3)
            self.handle = f"h-{token}"

    def held(self, course: str) -> Section | None:
        return next((s for s in self.schedule if s.course == course), None)

    def review(self, want: Want, catalog: list[Section]) -> tuple[list[Section], list[Skipped]]:
        """Sections this student would accept for `want`, and the ones ruled out by their constraints."""
        held = self.held(want.course)
        if held is None:
            return [], []
        others = [s for s in self.schedule if s is not held]
        accepted: list[Section] = []
        skipped: list[Skipped] = []
        for section in catalog:
            if section.class_nbr == held.class_nbr or section.component != held.component:
                continue
            if section.campus != held.campus or not want.matches(section):
                continue
            clash = next((o for o in others if section.clashes(o)), None)
            if clash is not None:
                skipped.append(Skipped(section, f"clashes with your {clash.course} ({clash.when()})"))
            elif (why := self.prefs.conflict(section)) is not None:
                skipped.append(Skipped(section, why))
            else:
                accepted.append(section)
        return accepted, skipped

    def offer(self, want: Want, catalog: list[Section]) -> Offer | None:
        """The offer for one want, or None when nothing acceptable is left after the constraints."""
        held = self.held(want.course)
        accepted, _ = self.review(want, catalog)
        if held is None or not accepted:
            return None
        return Offer(self.handle, want.course, held.class_nbr, tuple(s.class_nbr for s in accepted))

    def ask(self, kind: str, context: dict) -> str:
        return self.answer(kind, context)
