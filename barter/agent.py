"""A student's personal agent.

Everything here is private to one student: their name, contact, the value floor for what
they give, what things are worth to them, when they're free, which spots they'll meet at,
and why. What leaves the agent: one `Offer` (handle + item listing), and yes/no answers.
"""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, field
from typing import Callable

from .meetup import Block, Slot
from .models import Item, Listing, Offer

# A student's answer to a question. kind: "loop", "meetup", "handoff" (and "anchor", "plan",
# "step" for section swaps). Returns "yes"/"no", or for handoff "got it"/"didn't get it"/"noshow".
Answerer = Callable[[str, dict], str]


@dataclass
class Decision:
    """An edge answer and the private reason, which only the student sees."""

    yes: bool
    why: str


@dataclass
class StudentAgent:
    name: str
    listing: Listing
    contact: str = ""  # never shared; the bot relays meetup messages
    values: dict[str, float] = field(default_factory=dict)  # category -> what it's worth to me
    availability: list[Block] = field(default_factory=list)
    spots: tuple[str, ...] = ()  # campus spots I'll meet at; empty means any
    reasons: str = ""
    answer: Answerer = lambda kind, context: "yes"
    rng: random.Random | None = None
    handle: str = ""
    decisions: list[tuple[str, Decision]] = field(default_factory=list)  # private log

    def __post_init__(self) -> None:
        if not self.handle:
            token = f"{self.rng.getrandbits(24):06x}" if self.rng else secrets.token_hex(3)
            self.handle = f"h-{token}"

    def offer(self) -> Offer:
        return Offer(self.handle, self.listing.have, tuple(self.listing.wants))

    def value_of(self, item: Item) -> float:
        return self.values.get(item.category, item.est_value)

    # ------------------------------------------------------------ the questions the exchange asks

    def decide_edge(self, item: Item, gives: bool) -> Decision:
        """Would I take `item`, giving up my own item (gives=True) or nothing (gives=False)?"""
        want = next((w for w in self.listing.wants if w.matches(item)), None)
        if want is None:
            reasons = "; ".join(filter(None, (w.why_not(item) for w in self.listing.wants)))
            return Decision(False, f"it doesn't match what you asked for ({reasons})")
        if not gives or self.listing.floor is None:
            return Decision(True, f"it matches what you asked for ({want.label()})")
        worth = self.value_of(item)
        if worth >= self.listing.floor:
            return Decision(True, f"it's worth ${worth:g} to you, at or above your ${self.listing.floor:g} floor")
        return Decision(False, f"it's worth ${worth:g} to you, below your ${self.listing.floor:g} floor")

    def accepts(self, item: Item, gives: bool) -> bool:
        decision = self.decide_edge(item, gives)
        self.decisions.append((item.label(), decision))
        return decision.yes

    def free(self, slot: Slot) -> bool:
        return any(block.covers(slot) for block in self.availability)

    def spot_ok(self, spot: str) -> bool:
        return not self.spots or spot in self.spots

    def ask(self, kind: str, context: dict) -> str:
        return self.answer(kind, context)
