"""Proposal lifecycle and the execution plan.

A cycle is a tuple of offers in order: member i gets the seat member i+1 holds
(the last member gets the first member's seat).

    PROPOSED -> ACCEPTED_BY_ALL -> PLANNED -> RUNNING -> DONE
       any "no", no anchor, a "no" to the plan -> CANCELLED
       a failed or timed-out step              -> FAILED
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .agent import Offer

STEP_GAP = 5  # seconds between countdown steps
STEP_TIMEOUT = 60  # seconds a student has to report a step


class State(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED_BY_ALL = "ACCEPTED_BY_ALL"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Step:
    at: int  # seconds after T+0
    handle: str
    action: str  # "swap" (atomic drop+add), "drop" or "add"
    from_nbr: str | None
    to_nbr: str | None

    def payload(self) -> dict:
        return {"at": self.at, "handle": self.handle, "action": self.action, "from": self.from_nbr, "to": self.to_nbr}

    def describe(self) -> str:
        if self.action == "swap":
            return f"Swap #{self.from_nbr} for #{self.to_nbr} (ASU's Swap: one atomic drop+add)"
        if self.action == "drop":
            return f"Drop #{self.from_nbr}"
        return f"Add #{self.to_nbr}"


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]
    anchor: str | None  # handle that drops first, or None when every step is an atomic Swap
    risk: str  # one plain line

    def payload(self) -> dict:
        return {"anchor": self.anchor, "risk": self.risk, "steps": [s.payload() for s in self.steps]}


def needs_anchor(cycle: tuple[Offer, ...], open_seat: dict[str, bool]) -> bool:
    """True when no member's target has an open seat, so every atomic Swap would fail (deadlock)."""
    return not any(open_seat.get(target(cycle, i), False) for i in range(len(cycle)))


def target(cycle: tuple[Offer, ...], i: int) -> str:
    return cycle[(i + 1) % len(cycle)].have


def make_plan(cycle: tuple[Offer, ...], open_seat: dict[str, bool], anchor: str | None = None) -> Plan:
    """Order the steps so each seat is freed just before the student who wants it moves.

    Member i frees cycle[i].have, which member i-1 wants, so the chain runs backwards.
    """
    n = len(cycle)
    handles = [o.handle for o in cycle]
    starter = next((i for i in range(n) if open_seat.get(target(cycle, i), False)), None)
    if starter is not None:
        steps = []
        for k in range(n):
            i = (starter - k) % n
            steps.append(Step(k * STEP_GAP, handles[i], "swap", cycle[i].have, target(cycle, i)))
        return Plan(tuple(steps), None, "None: every step is an atomic Swap. If one fails, that student keeps their seat.")
    if anchor is None:
        raise ProtocolError("every section in the cycle is full: an anchor must opt in to drop first")
    a = handles.index(anchor)
    steps = [Step(0, anchor, "drop", cycle[a].have, None)]
    for k in range(1, n):
        i = (a - k) % n
        steps.append(Step(k * STEP_GAP, handles[i], "swap", cycle[i].have, target(cycle, i)))
    steps.append(Step(n * STEP_GAP, anchor, "add", None, target(cycle, a)))
    window = n * STEP_GAP
    risk = (
        f"The anchor ({anchor}) holds no {cycle[a].course} seat for about {window} seconds: if someone outside "
        "the swap takes a freed seat first, the chain stops and the anchor may end up with no section."
    )
    return Plan(tuple(steps), anchor, risk)


@dataclass
class Proposal:
    id: str
    cycle: tuple[Offer, ...]
    state: State = State.PROPOSED
    votes: dict[str, bool] = field(default_factory=dict)
    plan_votes: dict[str, bool] = field(default_factory=dict)
    plan: Plan | None = None
    done_steps: int = 0
    reason: str = ""

    @property
    def course(self) -> str:
        return self.cycle[0].course

    @property
    def handles(self) -> list[str]:
        return [o.handle for o in self.cycle]

    def gives(self, handle: str) -> str:
        return self.cycle[self.handles.index(handle)].have

    def gets(self, handle: str) -> str:
        return target(self.cycle, self.handles.index(handle))

    def payload(self) -> dict:
        """What the exchange sends each member: the offers in cycle order. Member i gets member i+1's `have`."""
        return {"proposal": self.id, "cycle": [o.payload() for o in self.cycle]}

    def _require(self, *states: State) -> None:
        if self.state not in states:
            raise ProtocolError(f"{self.id} is {self.state.value}, expected {'/'.join(s.value for s in states)}")

    def _cancel(self, reason: str) -> None:
        self.state, self.reason = State.CANCELLED, reason

    # (a) confirm the proposed swap
    def vote(self, handle: str, yes: bool) -> None:
        self._require(State.PROPOSED)
        self.votes[handle] = yes
        if not yes:
            self._cancel("someone in the cycle said no to the swap")
        elif all(self.votes.get(h) for h in self.handles):
            self.state = State.ACCEPTED_BY_ALL

    # the execution plan, with an anchor when every seat is full
    def make_plan(self, open_seat: dict[str, bool], anchor: str | None = None) -> Plan | None:
        self._require(State.ACCEPTED_BY_ALL)
        if needs_anchor(self.cycle, open_seat) and anchor is None:
            self._cancel("every section is full and nobody opted in to drop first")
            return None
        self.plan = make_plan(self.cycle, open_seat, anchor)
        self.state = State.PLANNED
        return self.plan

    # (b) confirm the plan and its risk
    def confirm_plan(self, handle: str, yes: bool) -> None:
        self._require(State.PLANNED)
        self.plan_votes[handle] = yes
        if not yes:
            self._cancel("someone in the cycle said no to the plan")
        elif all(self.plan_votes.get(h) for h in self.handles):
            self.state = State.RUNNING

    @property
    def next_step(self) -> Step | None:
        if self.state is not State.RUNNING or self.plan is None:
            return None
        return self.plan.steps[self.done_steps]

    # (c) "done" after each step
    def report(self, handle: str, status: str) -> None:
        self._require(State.RUNNING)
        step = self.next_step
        if step is None or step.handle != handle:
            raise ProtocolError(f"{handle} reported out of turn")
        if status == "done":
            self.done_steps += 1
            if self.done_steps == len(self.plan.steps):
                self.state = State.DONE
        else:
            self.state = State.FAILED
            self.reason = f"step {self.done_steps + 1} ({handle}: {step.describe()}) {status}"

    def recovery(self, open_seat: dict[str, bool]) -> dict[str, str]:
        """After a failure: what each member should do now, keyed by handle."""
        self._require(State.FAILED)
        done = self.plan.steps[: self.done_steps]
        failed = self.plan.steps[self.done_steps]
        out = {}
        for h in self.handles:
            mine = [s for s in done if s.handle == h]
            if h == failed.handle and failed.action == "swap":
                out[h] = (
                    f"Your Swap didn't go through, so you should still hold #{failed.from_nbr}. Check your "
                    f"schedule. If it did go through after all, you hold #{failed.to_nbr}: keep it."
                )
            elif h == failed.handle and failed.action == "add":
                out[h] = _seatless(self.gives(h), failed.to_nbr, open_seat)
            elif any(s.action == "swap" for s in mine):
                out[h] = f"You moved to #{self.gets(h)}, which is what you wanted. Keep it; nothing to undo."
            elif any(s.action == "drop" for s in mine):
                out[h] = _seatless(self.gives(h), self.gets(h), open_seat)
            else:
                out[h] = f"Do nothing. You still hold #{self.gives(h)}. The swap is off."
        return out


def _seatless(original: str, wanted: str, open_seat: dict[str, bool]) -> str:
    if open_seat.get(wanted, False):
        return f"You hold no seat right now. #{wanted} is still open: add it now."
    if open_seat.get(original, False):
        return f"You hold no seat right now. Your old #{original} is open again: add it back now."
    return (
        f"You hold no seat right now and #{wanted} is full. Add any open section of the course you can take, "
        f"and join the waitlist for #{wanted}. I'm sorry: this is the risk you opted in to."
    )
