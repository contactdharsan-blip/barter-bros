"""The exchange: an offer book and a matcher.

It only ever sees offers (handle, course, have, accept). Edge A -> B when B's
`have` is in A's `accept`, i.e. A would take B's seat. A cycle is a swap everyone
in it wants.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from itertools import count

from .agent import Offer
from .protocol import Proposal, State

MAX_CYCLE = 3
LIVE = {State.PROPOSED, State.ACCEPTED_BY_ALL, State.PLANNED}


@dataclass(frozen=True)
class Message:
    t: int  # simulated seconds
    sender: str
    recipient: str
    kind: str
    payload: dict


def find_cycles(offers: list[Offer], max_len: int = MAX_CYCLE) -> list[tuple[Offer, ...]]:
    """Every swap cycle of length 2..max_len, each once, rotated to start at its first-listed offer."""
    index = {id(o): i for i, o in enumerate(offers)}
    edges = {
        id(a): [b for b in offers if b is not a and b.course == a.course and b.have in a.accept] for a in offers
    }
    found: dict[tuple[int, ...], tuple[Offer, ...]] = {}

    def walk(path: list[Offer]) -> None:
        for nxt in edges[id(path[-1])]:
            if nxt is path[0] and len(path) >= 2:
                start = min(range(len(path)), key=lambda i: index[id(path[i])])
                cycle = tuple(path[start:] + path[:start])
                found.setdefault(tuple(index[id(o)] for o in cycle), cycle)
            elif nxt not in path and len(path) < max_len:
                walk(path + [nxt])

    for offer in offers:
        walk([offer])
    return list(found.values())


def choose(cycles: list[tuple[Offer, ...]], seq: dict[tuple[str, str], int]) -> list[tuple[Offer, ...]]:
    """Disjoint cycles: shorter first, then the earliest offers (FIFO), then handles as a tie-break."""

    def priority(cycle: tuple[Offer, ...]):
        return (len(cycle), sorted(seq[(o.handle, o.course)] for o in cycle), [o.handle for o in cycle])

    taken: set[tuple[str, str]] = set()
    chosen = []
    for cycle in sorted(cycles, key=priority):
        keys = {(o.handle, o.course) for o in cycle}
        if keys & taken:
            continue
        taken |= keys
        chosen.append(cycle)
    return chosen


def _cycle_key(cycle: tuple[Offer, ...]) -> frozenset:
    return frozenset((o.handle, o.course, o.have) for o in cycle)


@dataclass
class Exchange:
    book: dict[tuple[str, str], Offer] = field(default_factory=dict)
    seq: dict[tuple[str, str], int] = field(default_factory=dict)
    locked: set[tuple[str, str]] = field(default_factory=set)
    declined: set[frozenset] = field(default_factory=set)
    proposals: dict[str, Proposal] = field(default_factory=dict)
    log: list[Message] = field(default_factory=list)
    _counter: count = field(default_factory=count)
    _ids: count = field(default_factory=lambda: count(1))

    def transmit(self, t: int, sender: str, recipient: str, kind: str, payload: dict) -> Message:
        """Every message that crosses the exchange goes through here, as JSON, and is logged."""
        msg = Message(t, sender, recipient, kind, json.loads(json.dumps(payload)))
        self.log.append(msg)
        return msg

    def add(self, payload: dict) -> list[Proposal]:
        offer = Offer.from_payload(payload)
        key = (offer.handle, offer.course)
        self.book[key] = offer
        self.seq.setdefault(key, next(self._counter))
        return self.recompute()

    def withdraw(self, handle: str, course: str) -> list[Proposal]:
        """Take an offer down. A live proposal it was part of is cancelled and its other members freed."""
        key = (handle, course)
        for p in self.proposals.values():
            if p.state in LIVE and key in {(o.handle, o.course) for o in p.cycle}:
                p.state, p.reason = State.CANCELLED, "someone in the cycle withdrew"
                self.locked -= {(o.handle, o.course) for o in p.cycle}
        self.book.pop(key, None)
        self.seq.pop(key, None)
        self.locked.discard(key)
        return self.recompute()

    def open_offers(self) -> list[Offer]:
        keys = sorted((k for k in self.book if k not in self.locked), key=self.seq.__getitem__)
        return [self.book[k] for k in keys]

    def recompute(self) -> list[Proposal]:
        """New proposals from the offers not already in a live proposal."""
        cycles = [c for c in find_cycles(self.open_offers()) if _cycle_key(c) not in self.declined]
        new = []
        for cycle in choose(cycles, self.seq):
            proposal = Proposal(f"P{next(self._ids)}", cycle)
            self.proposals[proposal.id] = proposal
            self.locked |= {(o.handle, o.course) for o in cycle}
            new.append(proposal)
        return new

    def close(self, proposal: Proposal) -> list[Proposal]:
        """Release a finished proposal. A cancelled cycle is never proposed again; a done or failed
        one takes its offers off the book (the seats changed hands, or may have)."""
        keys = {(o.handle, o.course) for o in proposal.cycle}
        self.locked -= keys
        if proposal.state is State.CANCELLED:
            self.declined.add(_cycle_key(proposal.cycle))
        else:
            for key in keys:
                self.book.pop(key, None)
                self.seq.pop(key, None)
        return self.recompute()
