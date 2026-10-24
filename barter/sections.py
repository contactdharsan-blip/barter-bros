"""ASU class sections as barter items, wrapping the Section Swap engine in `swap/`.

`swap/` is left untouched and keeps its own simulator and tests. This module:
- turns a seat a student holds into an `Item("section", {course, class_nbr})`;
- turns a swap want into a barter `Want` whose `class_nbr` is the list of sections the
  student's agent accepts after its private checks (clashes with their other classes, time
  preferences), using `swap.agent.StudentAgent.review`. The exchange sees only that list;
- runs an accepted section loop through swap's countdown plan (`swap.protocol`), which knows
  about full sections and the anchor.

Sections only trade for sections of the same course; a section never meets a thing in a loop.
"""

from __future__ import annotations

from swap.agent import Offer as SwapOffer
from swap.agent import Skipped, StudentAgent as SwapStudent
from swap.models import Section
from swap.models import Want as SwapWant
from swap.protocol import Proposal

from .match import Loop
from .models import Item, Listing, Want


def section_item(section: Section) -> Item:
    return Item("section", {"course": section.course, "class_nbr": section.class_nbr}, "good")


def listing_for(student: SwapStudent, want: SwapWant, catalog: list[Section]) -> tuple[Listing | None, list[Section], list[Skipped]]:
    """(listing or None when nothing acceptable is left, accepted sections, ruled-out sections)."""
    held = student.held(want.course)
    accepted, skipped = student.review(want, catalog)
    if held is None or not accepted:
        return None, accepted, skipped
    wanted = Want("section", {"course": want.course, "class_nbr": tuple(s.class_nbr for s in accepted)})
    return Listing(have=section_item(held), wants=[wanted], parser="sections"), accepted, skipped


def swap_proposal(trade_id: str, loop: Loop, have: dict[str, str], accept: dict[str, tuple[str, ...]],
                  course: str) -> Proposal:
    """The swap engine's view of an accepted barter loop, already voted yes by everyone.

    Barter loops run "member i gives to member i+1"; swap cycles run "member i gets member i+1's
    seat". So the swap cycle is the barter loop reversed.
    """
    order = list(reversed(loop.members))
    cycle = tuple(SwapOffer(h, course, have[h], accept[h]) for h in order)
    proposal = Proposal(trade_id, cycle)
    for h in order:
        proposal.vote(h, True)
    return proposal
