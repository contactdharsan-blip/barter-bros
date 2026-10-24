"""Run a scenario and print what every agent tells its student and what crosses the exchange.

    python -m swap.sim scenarios/chem-3way.json

Simulated time only; nothing sleeps and nothing touches the network.
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TextIO

from . import asu
from .agent import StudentAgent
from .exchange import Exchange, Proposal
from .models import Meeting, Section, TimePrefs, Want, parse_clock
from .protocol import STEP_GAP, STEP_TIMEOUT, State, Step

ROOT = Path(__file__).resolve().parent.parent
REPLY = 30  # simulated seconds a student takes to answer a question
REPORT = 3  # seconds to report a step


# ---------------------------------------------------------------- scenario loading


def _want(spec: dict) -> Want:
    return Want(
        course=spec["course"],
        sections=frozenset(spec["sections"]) if "sections" in spec else None,
        days_in=frozenset(spec["days_in"]) if "days_in" in spec else None,
        start_at_or_after=parse_clock(spec["start_at_or_after"]) if "start_at_or_after" in spec else None,
        end_by=parse_clock(spec["end_by"]) if "end_by" in spec else None,
    )


def _prefs(spec: dict) -> TimePrefs:
    return TimePrefs(
        not_before=parse_clock(spec["not_before"]) if "not_before" in spec else None,
        not_after=parse_clock(spec["not_after"]) if "not_after" in spec else None,
        days_off=frozenset(spec.get("days_off", ())),
    )


def _extra_section(spec: dict) -> Section:
    meeting = Meeting(frozenset(spec["days"]), parse_clock(spec["start"]), parse_clock(spec["end"]))
    return Section(
        spec["class_nbr"], spec["course"], spec["section"], spec["component"], (meeting,),
        spec["taken"], spec["capacity"], spec.get("campus", ""), spec.get("instructor"),
    )


def load_catalog(scenario: dict) -> list[Section]:
    sections = asu.load_fixture(ROOT / scenario["fixture"])
    overrides = scenario.get("seat_overrides", {})
    sections = [replace(s, seats_taken=overrides[s.class_nbr]) if s.class_nbr in overrides else s for s in sections]
    return sections + [_extra_section(x) for x in scenario.get("extra_sections", [])]


def scripted(answers: dict):
    defaults = {"proposal": "yes", "anchor": "no", "plan": "yes"}

    def answer(kind: str, context: dict) -> str:
        if kind == "step":
            return answers.get("step", context["result"])
        return answers.get(kind, defaults[kind])

    return answer


# ---------------------------------------------------------------- simulated registration


@dataclass
class Registrar:
    """Seat counts as ASU would hold them. Students act on it; agents never do."""

    seats: dict[str, list[int]]  # class_nbr -> [taken, capacity]

    def is_open(self, nbr: str) -> bool:
        taken, cap = self.seats[nbr]
        return taken < cap

    def open_map(self) -> dict[str, bool]:
        return {nbr: self.is_open(nbr) for nbr in self.seats}

    def apply(self, step: Step) -> tuple[bool, str]:
        if step.action in ("swap", "add") and not self.is_open(step.to_nbr):
            return False, f"#{step.to_nbr} is full"
        if step.from_nbr:
            self.seats[step.from_nbr][0] -= 1
        if step.to_nbr:
            self.seats[step.to_nbr][0] += 1
        return True, ""

    def show(self, nbr: str) -> str:
        taken, cap = self.seats[nbr]
        return f"#{nbr} {taken}/{cap}"


# ---------------------------------------------------------------- the run


@dataclass
class Result:
    exchange: Exchange
    agents: list[StudentAgent]
    registrar: Registrar
    proposals: list[Proposal] = field(default_factory=list)


class Sim:
    def __init__(self, scenario: dict, out: TextIO):
        self.scenario = scenario
        self.out = out
        self.t = 0
        self.fixture_taken = {s.class_nbr: s.seats_taken for s in asu.load_fixture(ROOT / scenario["fixture"])}
        self.catalog = load_catalog(scenario)
        self.by_nbr = {s.class_nbr: s for s in self.catalog}
        self.registrar = Registrar({s.class_nbr: [s.seats_taken, s.capacity] for s in self.catalog})
        self.exchange = Exchange()
        rng = random.Random(scenario.get("seed", 0))
        self.agents: list[StudentAgent] = []
        for spec in scenario["students"]:
            self.agents.append(
                StudentAgent(
                    name=spec["name"],
                    schedule=[self.by_nbr[n] for n in spec["holds"]],
                    wants=[_want(w) for w in spec["wants"]],
                    prefs=_prefs(spec.get("prefs", {})),
                    reasons=spec.get("reasons", {}),
                    answer=scripted(spec.get("answers", {})),
                    rng=rng,
                )
            )
        self.by_handle = {a.handle: a for a in self.agents}
        self.events = scenario.get("events", [])
        self.result = Result(self.exchange, self.agents, self.registrar)

    # -- output helpers

    def clock(self) -> str:
        h, rem = divmod(9 * 3600 + self.t, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def dm(self, agent: StudentAgent, text: str) -> None:
        head = f"[{self.clock()}] agent -> {agent.name}: "
        lines = text.split("\n")
        self.say(head + lines[0])
        for line in lines[1:]:
            self.say(" " * len(head) + line)

    def reply(self, agent: StudentAgent, text: str) -> None:
        self.say(f"[{self.clock()}] {agent.name} -> agent: {text}")

    def wire(self, sender: str, recipient: str, kind: str, payload: dict) -> None:
        msg = self.exchange.transmit(self.t, sender, recipient, kind, payload)
        self.say(f"[{self.clock()}]   WIRE {sender} => {recipient} {kind} {json.dumps(msg.payload)}")

    def note(self, text: str) -> None:
        self.say(f"[{self.clock()}]   exchange: {text}")

    def section(self, nbr: str) -> str:
        return self.by_nbr[nbr].label()

    # -- phases

    def run(self) -> Result:
        sc = self.scenario
        self.say(f"=== Section Swap simulator: {sc['name']} ===")
        self.say(sc["about"])
        if sc.get("seat_overrides"):
            self.say(f"Seat overrides: {sc['seat_override_note']}")
            for nbr, taken in sc["seat_overrides"].items():
                real = self.fixture_taken[nbr]
                self.say(f"  #{nbr}: set to {taken}/{self.by_nbr[nbr].capacity} (fixture has {real})")
        self.say("Times are simulated. Everyone below is invented; the CHM 113 sections are real (term 2271).")
        for agent in self.agents:
            self.say()
            self.join(agent)
            self.t += 60
        self.say()
        self.say("=== End of run ===")
        for p in self.result.proposals:
            self.say(f"{p.id} {' -> '.join(p.handles)}: {p.state.value}{f' ({p.reason})' if p.reason else ''}")
        for key in self.exchange.open_offers():
            self.say(f"Still listed, waiting for a match: {key.handle} (has #{key.have}, accepts {list(key.accept)})")
        return self.result

    def join(self, agent: StudentAgent) -> None:
        self.say(f"--- {agent.name} starts their agent ---")
        for want in agent.wants:
            held = agent.held(want.course)
            reason = agent.reasons.get(want.course)
            self.dm(agent, f"You hold {held.label()}." + (f' You told me: "{reason}"' if reason else ""))
            accepted, skipped = agent.review(want, self.catalog)
            for s in skipped:
                self.dm(agent, f"Ruled out {s.section.label()}: {s.reason}.")
            offer = agent.offer(want, self.catalog)
            if offer is None:
                self.dm(agent, "Nothing you asked for fits around your other classes and preferences, so I "
                               "won't offer anything to the exchange. Nothing about you was shared.")
                continue
            self.dm(agent, "You'd accept: " + "; ".join(s.label() for s in accepted) + ".")
            self.dm(agent, f"I'm posting this under the random handle {agent.handle}: the course, the section you "
                           "hold and the sections you'd accept. Not your name, your other classes or your reasons.")
            self.wire(agent.handle, "exchange", "offer", offer.payload())
            new = self.exchange.add(offer.payload())
            self.note(f"{len(self.exchange.book)} offer(s) in the book; "
                      + (f"found {', '.join(self.describe(p) for p in new)}" if new else "no cycle yet"))
            self.contention(new)
            self.process(new)

    def contention(self, new: list[Proposal]) -> None:
        """Say who else wanted a seat in a chosen cycle and why they wait."""
        seats = {o.have for p in new for o in p.cycle}
        for o in self.exchange.open_offers():
            wanted = sorted(seats & set(o.accept))
            if wanted:
                self.note(f"{o.handle} also accepts #{', #'.join(wanted)}, but no cycle closes through it, so it "
                          "stays listed. (Each offer joins at most one cycle; shorter cycles first, then first-come.)")

    def describe(self, p: Proposal) -> str:
        return f"{p.id}, a {len(p.cycle)}-way cycle " + " -> ".join(f"{o.handle}(#{o.have})" for o in p.cycle)

    def process(self, proposals: list[Proposal]) -> None:
        queue = list(proposals)
        while queue:
            p = queue.pop(0)
            self.result.proposals.append(p)
            self.run_proposal(p)
            queue.extend(self.exchange.close(p))

    def ask(self, agent: StudentAgent, kind: str, question: str, context: dict | None = None) -> str:
        self.dm(agent, question)
        self.t += REPLY
        answer = agent.ask(kind, context or {})
        self.reply(agent, answer)
        return answer

    def members(self, p: Proposal) -> list[StudentAgent]:
        return [self.by_handle[h] for h in p.handles]

    def broadcast_cancel(self, p: Proposal) -> None:
        for a in self.members(p):
            self.wire("exchange", a.handle, "cancelled", {"proposal": p.id, "reason": p.reason})
        for a in self.members(p):
            self.dm(a, f"The swap is off: {p.reason}. Nobody moves. You keep #{p.gives(a.handle)}. "
                       "Your offer stays listed, and this exact cycle won't be proposed again.")

    def run_proposal(self, p: Proposal) -> None:
        self.say()
        self.say(f"--- {p.id}: confirmation (a), the proposed swap ---")
        for a in self.members(p):
            self.wire("exchange", a.handle, "proposal", p.payload())
        for a in self.members(p):
            others = len(p.cycle) - 1
            answer = self.ask(
                a, "proposal",
                f"Swap found ({len(p.cycle)}-way). You give {self.section(p.gives(a.handle))} and get "
                f"{self.section(p.gets(a.handle))}. The other {others} student(s) are shown to me only as "
                "handles. Nothing more about you is shared whether you say yes or no, and one no cancels it for "
                "everyone. Do you want this swap? (yes/no)",
            )
            self.wire(a.handle, "exchange", "vote", {"proposal": p.id, "handle": a.handle, "answer": answer})
            p.vote(a.handle, answer == "yes")
            if p.state is State.CANCELLED:
                self.broadcast_cancel(p)
                return

        open_seat = self.registrar.open_map()
        anchor = None
        targets = ", ".join(self.registrar.show(p.gets(h)) for h in p.handles)
        self.say()
        if all(not open_seat[p.gets(h)] for h in p.handles):
            self.say(f"--- {p.id}: every target is full ({targets}), so no atomic Swap can succeed. "
                     "Someone must drop first ---")
            for a in self.members(p):
                self.wire("exchange", a.handle, "anchor?", {"proposal": p.id})
                answer = self.ask(
                    a, "anchor",
                    f"Every section in this swap is full, so ASU's Swap fails for all of you: each seat you want is "
                    f"held by someone who wants yours. One person has to drop first. The anchor drops "
                    f"#{p.gives(a.handle)} at T+0 and adds #{p.gets(a.handle)} last, about "
                    f"{len(p.cycle) * STEP_GAP} s later. In between you hold no seat and a stranger could take one. "
                    "Will you be the anchor? (yes/no)",
                )
                self.wire(a.handle, "exchange", "anchor", {"proposal": p.id, "handle": a.handle, "answer": answer})
                if answer == "yes":
                    anchor = a.handle
                    break
        else:
            self.say(f"--- {p.id}: an open seat breaks the deadlock ({targets}); no anchor needed ---")

        plan = p.make_plan(open_seat, anchor)
        if plan is None:
            self.broadcast_cancel(p)
            return

        self.say()
        self.say(f"--- {p.id}: confirmation (b), the plan and its risk ---")
        start = self.t + len(p.cycle) * REPLY + 60
        for a in self.members(p):
            self.wire("exchange", a.handle, "plan", {"proposal": p.id, **plan.payload()})
        for a in self.members(p):
            lines = []
            for s in plan.steps:
                who = "YOU" if s.handle == a.handle else s.handle
                lines.append(f"  T+{s.at:<2} s  {who}: {s.describe()}")
            mine = "You are the anchor: that risk is yours." if a.handle == plan.anchor else (
                "Your step is an atomic Swap: if it fails you keep your seat.")
            answer = self.ask(
                a, "plan",
                "Here's the plan. Open ASU registration and have it ready; I'll count you in.\n"
                + "\n".join(lines) + f"\nRisk: {plan.risk}\n{mine} Go ahead? (yes/no)",
            )
            self.wire(a.handle, "exchange", "plan-vote", {"proposal": p.id, "handle": a.handle, "answer": answer})
            p.confirm_plan(a.handle, answer == "yes")
            if p.state is State.CANCELLED:
                self.broadcast_cancel(p)
                return

        self.t = max(self.t, start)
        self.say()
        self.say(f"--- {p.id}: countdown, confirmation (c) after every step. T+0 = {self.clock()} ---")
        t0 = self.t
        for i, step in enumerate(plan.steps, 1):
            self.t = max(self.t, t0 + step.at)
            a = self.by_handle[step.handle]
            self.wire("exchange", a.handle, "go", {"proposal": p.id, "step": i})
            self.dm(a, f"T+{step.at} s. Now: {step.describe()}. Tell me when it's done.")
            ok, why = self.registrar.apply(step)
            self.t += REPORT
            status = a.ask("step", {"result": "done" if ok else "failed"})
            if status == "timeout":
                self.t = t0 + step.at + STEP_TIMEOUT
                self.note(f"no report from {a.handle} for step {i} after {STEP_TIMEOUT} s")
                p.report(a.handle, "timed out")
            else:
                self.reply(a, status + (f" ({why})" if why else ""))
                self.wire(a.handle, "exchange", "step", {"proposal": p.id, "handle": a.handle, "step": i,
                                                         "status": status})
                p.report(a.handle, status)
                touched = [n for n in (step.from_nbr, step.to_nbr) if n]
                self.say(f"{'':11}  seats now: {', '.join(self.registrar.show(n) for n in touched)}")
            for event in self.events:
                if event.get("after_step") == i and p.state is State.RUNNING:
                    nbr = event["stranger_adds"]
                    self.registrar.seats[nbr][0] += 1
                    self.t += 1
                    self.say(f"[{self.clock()}]   (outside the swap) a stranger adds #{nbr}: "
                             f"{self.registrar.show(nbr)}")
            if p.state is not State.RUNNING:
                break

        self.say()
        if p.state is State.DONE:
            for a in self.members(p):
                self.wire("exchange", a.handle, "done", {"proposal": p.id})
            for a in self.members(p):
                self.dm(a, f"Done. You're in {self.section(p.gets(a.handle))}. Check your ASU schedule once to be sure.")
            return
        self.say(f"--- {p.id}: FAILED, {p.reason}. Recovery ---")
        recovery = p.recovery(self.registrar.open_map())
        for a in self.members(p):
            self.wire("exchange", a.handle, "stopped", {"proposal": p.id, "step": p.done_steps + 1})
        for a in self.members(p):
            self.dm(a, f"Stop: the chain broke at step {p.done_steps + 1}. {recovery[a.handle]}")


def run(path: str | Path, out: TextIO = sys.stdout) -> Result:
    scenario = json.loads(Path(path).read_text())
    return Sim(scenario, out).run()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m swap.sim scenarios/<name>.json", file=sys.stderr)
        return 2
    run(argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
