"""Profiles: who you're meeting, revealed only once everyone in a loop has accepted.

A profile is what a student chooses to show a trade partner: display name, an avatar (initials on
a palette colour), campus, a short bio, member-since, and a trade record (completed trades,
showed up, no-shows). It never holds contact info, a value floor or a reason; those stay in the
agent, at every stage.

Before every member of a loop says yes, partners see only handles and item listings. When the
last yes arrives, the exchange sends each member an `identity` message with the others' public
profiles (exactly PUBLIC_KEYS). Everyone in the demo is invented, and no demo bio restates the
person's private reason (the privacy tests check that the reasons never cross).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

COLORS = ("blue", "coral", "lime", "purple", "yellow")
PUBLIC_KEYS = ("handle", "name", "initials", "color", "campus", "bio", "member_since", "completed", "showed_up",
               "no_shows")


@dataclass
class Profile:
    name: str
    campus: str = "ASU Tempe"
    bio: str = ""
    member_since: str = "Oct 2026"
    completed: int = 0  # trades handed off before this run, then counted live
    showed_up: int = 0
    no_shows: int = 0

    @property
    def initials(self) -> str:
        parts = [p for p in self.name.split() if p[:1].isalpha()]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    @property
    def color(self) -> str:
        return COLORS[int(hashlib.sha1(self.name.encode()).hexdigest(), 16) % len(COLORS)]

    def public(self, handle: str) -> dict:
        """What a partner sees once everyone in the loop has accepted. Exactly PUBLIC_KEYS."""
        return {"handle": handle, "name": self.name, "initials": self.initials, "color": self.color,
                "campus": self.campus, "bio": self.bio, "member_since": self.member_since,
                "completed": self.completed, "showed_up": self.showed_up, "no_shows": self.no_shows}


# Invented profiles for the demo people, by full name. Anyone else gets the defaults.
DEMO: dict[str, dict] = {
    "Jordan Rivera": {"bio": "Biology major, coffee first. Happy to meet at the library.", "member_since": "Aug 2026",
                      "completed": 2, "showed_up": 2},
    "Sam Okoye": {"bio": "Plays intramural soccer. Fast replies, careful with books.", "member_since": "Sep 2026",
                  "completed": 4, "showed_up": 4},
    "Priya Shah": {"campus": "ASU Tempe · Barrett", "bio": "CS sophomore. Always on time, usually early.",
                   "member_since": "Aug 2026", "completed": 6, "showed_up": 6},
    "Leo Marsh": {"bio": "Architecture student. Owns too many lamps.", "member_since": "Oct 2026", "completed": 1, "showed_up": 1},
    "Avery Lin": {"bio": "Econ junior. Meets between classes.", "completed": 3, "showed_up": 3},
    "Bo Patel": {"campus": "ASU Polytechnic", "bio": "Mechanical engineering. Weekends work best.", "completed": 1, "showed_up": 1},
    "Cam Ortiz": {"bio": "Film major. Will trade for anything sittable.", "completed": 2, "showed_up": 2},
    "Dana Webb": {"bio": "Pre-med. Usually at Noble Library.", "completed": 5, "showed_up": 4, "no_shows": 1},
    "Eli Novak": {"campus": "ASU Downtown Phoenix", "bio": "Nursing student. Commutes in on Saturdays.", "completed": 2,
                  "showed_up": 2},
    "Fern Ruiz": {"bio": "Senior in sociology. Loves a good giveaway.", "completed": 7, "showed_up": 7},
    "Gus Adler": {"bio": "Has a drawer full of cables.", "completed": 1, "showed_up": 1},
    "Hana Sato": {"bio": "Exchange student from Osaka.", "member_since": "Sep 2026"},
    "Ivy Moreau": {"bio": "Math minor, need a TI-Nspire.", "completed": 2, "showed_up": 2},
    "Jae Kim": {"bio": "Dorm decorator.", "completed": 1, "showed_up": 1},
    "Kai Mendes": {"campus": "ASU West", "bio": "Hiker. Need a backpack before spring break.", "completed": 0},
    "Lou Becker": {"bio": "Stats major. Punctual, mostly.", "completed": 3, "showed_up": 2,
                   "no_shows": 1},
}


def demo_profile(name: str) -> Profile:
    return Profile(name, **DEMO.get(name, {}))
