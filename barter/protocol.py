"""Trade lifecycle and the exchange's offer book.

    proposed -> accepted_by_all -> meetup_agreed -> handed_off
        any "no" to the loop, no common slot or spot, a "no" to the plan,
        a no-show or a "didn't get it"                     -> unwound

The three confirmations: (1) the loop, (2) the meetup plan, (3) "got it" from every
receiver at the handoff. An unwound trade re-lists everyone in it, and that exact loop is
never proposed again. A section-swap trade uses the same states; its "plan" is the drop/add
countdown from swap/protocol.py and its "got it" is the last countdown step done.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from itertools import count

from .match import EdgeAsk, Loop, find
from .models import Item, Offer


class State(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED_BY_ALL = "accepted_by_all"
    MEETUP_AGREED = "meetup_agreed"
    HANDED_OFF = "handed_off"
    UNWOUND = "unwound"


LIVE = {State.PROPOSED, State.ACCEPTED_BY_ALL, State.MEETUP_AGREED}


class ProtocolError(RuntimeError):
    pass


@dataclass
class Trade:
    id: str
    loop: Loop
    offers: dict[str, Offer]
    state: State = State.PROPOSED
    votes: dict[str, bool] = field(default_factory=dict)
    plan: object | None = None  # a meetup.Meetup, or a swap.protocol.Plan for sections
    plan_votes: dict[str, bool] = field(default_factory=dict)
    receipts: dict[str, bool] = field(default_factory=dict)
    reason: str = ""

    @property
    def handles(self) -> list[str]:
        return list(self.loop.members)

    @property
    def kind(self) -> str:
        """"countdown" when every item is a class section, else "meetup"."""
        items = [self.offers[h].have for h in self.handles if self.gives(h)]
        return "countdown" if all(i.category == "section" for i in items) else "meetup"

    def gives(self, handle: str) -> Item | None:
        return self.offers[handle].have if self.loop.receiver_from(handle) else None

    def gets(self, handle: str) -> Item | None:
        giver = self.loop.giver_to(handle)
        return self.offers[giver].have if giver else None

    def payload(self) -> dict:
        """What every member is sent: the listings in loop order. Member i gives to member i+1."""
        return {"trade": self.id, "shape": self.loop.kind, "loop": [self.offers[h].payload() for h in self.handles]}

    def _require(self, *states: State) -> None:
        if self.state not in states:
            raise ProtocolError(f"{self.id} is {self.state.value}, expected {'/'.join(s.value for s in states)}")

    def unwind(self, reason: str) -> None:
        self._require(*LIVE)
        self.state, self.reason = State.UNWOUND, reason

    # (1) the loop
    def vote(self, handle: str, yes: bool) -> None:
        self._require(State.PROPOSED)
        self.votes[handle] = yes
        if not yes:
            self.unwind("someone in the loop said no")
        elif all(self.votes.get(h) for h in self.handles):
            self.state = State.ACCEPTED_BY_ALL

    # (2) the meetup plan
    def propose_plan(self, plan: object) -> None:
        self._require(State.ACCEPTED_BY_ALL)
        self.plan, self.plan_votes = plan, {}

    def confirm_plan(self, handle: str, yes: bool) -> None:
        self._require(State.ACCEPTED_BY_ALL)
        if self.plan is None:
            raise ProtocolError(f"{self.id} has no plan to confirm")
        self.plan_votes[handle] = yes
        if not yes:
            self.unwind("someone said no to the meetup plan")
        elif all(self.plan_votes.get(h) for h in self.handles):
            self.state = State.MEETUP_AGREED

    # (3) "got it" at the handoff
    def receipt(self, handle: str, got_it: bool) -> None:
        self._require(State.MEETUP_AGREED)
        if handle not in self.loop.receivers():
            raise ProtocolError(f"{handle} receives nothing in {self.id}")
        self.receipts[handle] = got_it
        if not got_it:
            self.unwind(f"{handle} didn't get their item")
        elif all(self.receipts.get(h) for h in self.loop.receivers()):
            self.state = State.HANDED_OFF

    def no_show(self, handle: str) -> None:
        self._require(State.MEETUP_AGREED)
        self.unwind(f"{handle} didn't show up")


@dataclass(frozen=True)
class Message:
    t: int  # simulated seconds
    sender: str
    recipient: str
    kind: str
    payload: dict


@dataclass
class Exchange:
    """Holds offers (public listings) and trades. Never holds an agent or anything private."""

    book: dict[str, Offer] = field(default_factory=dict)
    seq: dict[str, int] = field(default_factory=dict)
    locked: set[str] = field(default_factory=set)
    declined: set[Loop] = field(default_factory=set)
    trades: dict[str, Trade] = field(default_factory=dict)
    answers: dict = field(default_factory=dict)  # cached edge yes/no
    log: list[Message] = field(default_factory=list)
    _counter: count = field(default_factory=count)
    _ids: count = field(default_factory=lambda: count(1))

    def transmit(self, t: int, sender: str, recipient: str, kind: str, payload: dict) -> Message:
        """Every message that crosses the exchange goes through here, as JSON, and is logged."""
        msg = Message(t, sender, recipient, kind, json.loads(json.dumps(payload)))
        self.log.append(msg)
        return msg

    def add(self, payload: dict) -> Offer:
        offer = Offer.from_payload(payload)
        self.book[offer.handle] = offer
        self.seq.setdefault(offer.handle, next(self._counter))
        return offer

    def open_offers(self) -> list[Offer]:
        keys = sorted((k for k in self.book if k not in self.locked), key=self.seq.__getitem__)
        return [self.book[k] for k in keys]

    def match(self, ask: EdgeAsk) -> list[Trade]:
        """New trades from the open offers. `ask` relays an edge question to the receiver's agent."""
        chosen, _ = find(self.open_offers(), self.seq, ask, self.declined, self.answers)
        new = []
        for loop in chosen:
            trade = Trade(f"T{next(self._ids)}", loop, {h: self.book[h] for h in loop.members})
            self.trades[trade.id] = trade
            self.locked |= set(loop.members)
            new.append(trade)
        return new

    def close(self, trade: Trade) -> None:
        """A handed-off trade takes everyone's listing off the book. An unwound one re-lists everyone
        and is never proposed again."""
        if trade.state in LIVE:
            raise ProtocolError(f"{trade.id} is still {trade.state.value}")
        self.locked -= set(trade.handles)
        if trade.state is State.UNWOUND:
            self.declined.add(trade.loop)
            return
        for h in trade.handles:
            self.book.pop(h, None)
            self.seq.pop(h, None)
