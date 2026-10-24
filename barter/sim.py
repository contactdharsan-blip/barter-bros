"""Run a Barter Bros scenario and print what every agent tells its student and what crosses the exchange.

    python -m barter.sim scenarios/book-charger-case-3way.json
    python -m barter.sim --claude scenarios/...   # parse with Claude if a key is configured

Simulated time only; nothing sleeps, and nothing touches the network or the Keychain unless
`--claude` is given.
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from swap.models import format_clock, parse_clock
from swap.protocol import STEP_GAP, STEP_TIMEOUT
from swap.protocol import State as SwapState
from swap.sim import Registrar, _prefs, _want, load_catalog

from . import parse as parser
from .agent import StudentAgent
from .meetup import Block, Window, negotiate
from .models import Item, Listing, Want
from .protocol import Exchange, State, Trade
from .sections import listing_for, swap_proposal

REPLY = 30  # simulated seconds a student takes to answer
DEFAULTS = {"loop": "yes", "meetup": "yes", "handoff": "got it", "anchor": "no", "plan": "yes"}


def scripted(answers: dict):
    def answer(kind: str, context: dict) -> str:
        if kind == "step":
            return answers.get("step", context["result"])
        return answers.get(kind, DEFAULTS[kind])

    return answer


@dataclass
class Result:
    exchange: Exchange
    agents: list[StudentAgent]
    trades: list[Trade] = field(default_factory=list)


class Sim:
    def __init__(self, scenario: dict, out: TextIO, backend: str = "rules"):
        self.scenario = scenario
        self.out = out
        self.client = parser.make_client() if backend == "auto" else None  # only with --claude
        self.backend = "claude" if self.client is not None else "rules"
        self.t = 0
        self.exchange = Exchange()
        sw = scenario.get("window", {"days": ["SA"], "start": "10:00 AM", "end": "4:00 PM"})
        self.window = Window(tuple(sw["days"]), parse_clock(sw["start"]), parse_clock(sw["end"]))
        self.catalog = load_catalog(scenario) if "fixture" in scenario else []
        self.by_nbr = {s.class_nbr: s for s in self.catalog}
        self.registrar = Registrar({s.class_nbr: [s.seats_taken, s.capacity] for s in self.catalog})
        self.rng = random.Random(scenario.get("seed", 0))
        self.specs = scenario["people"]
        self.agents: list[StudentAgent] = []
        self.by_handle: dict[str, StudentAgent] = {}
        self.result = Result(self.exchange, self.agents)

    # ------------------------------------------------------------ output

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

    def wire(self, sender: str, recipient: str, kind: str, payload: dict) -> dict:
        msg = self.exchange.transmit(self.t, sender, recipient, kind, payload)
        last = self.exchange.log[-2] if len(self.exchange.log) > 1 else None
        if last and last.kind == kind and last.sender == sender and last.payload == msg.payload and len(json.dumps(msg.payload)) > 80:
            body = "(same payload)"  # a broadcast: printed in full once
        else:
            body = json.dumps(msg.payload)
        self.say(f"[{self.clock()}]   WIRE {sender} => {recipient} {kind} {body}")
        return msg.payload

    def note(self, text: str) -> None:
        self.say(f"[{self.clock()}]   exchange: {text}")

    def ask(self, agent: StudentAgent, kind: str, question: str, context: dict | None = None) -> str:
        self.dm(agent, question)
        self.t += REPLY
        answer = agent.ask(kind, context or {})
        self.reply(agent, answer)
        return answer

    # ------------------------------------------------------------ the run

    def run(self) -> Result:
        sc = self.scenario
        self.say(f"=== Barter Bros simulator: {sc['name']} ===")
        self.say(sc["about"])
        if sc.get("seat_overrides"):
            self.say(f"Seat overrides: {sc['seat_override_note']}")
        self.say(f"Meetup window: {self.window.label()}. Times are simulated. Everyone below is invented.")
        for spec in self.specs:
            self.say()
            self.join(spec)
            self.t += 60
        self.say()
        self.say("=== End of run ===")
        for tr in self.result.trades:
            self.say(f"{tr.id} {tr.loop.describe()}: {tr.state.value}{f' ({tr.reason})' if tr.reason else ''}")
        for offer in self.exchange.open_offers():
            agent = self.by_handle[offer.handle]
            self.say(f"Still listed, waiting for a match: {offer.handle} ({self.listing_line(agent.listing)})")
        return self.result

    def listing_line(self, listing: Listing) -> str:
        have = listing.have.label() if listing.have else "nothing"
        wants = "; ".join(w.label() for w in listing.wants) or "nothing (a giveaway)"
        return f"has {have}; wants {wants}"

    def join(self, spec: dict) -> None:
        name = spec["name"]
        self.say(f"--- {name} starts their agent ---")
        if "section" in spec:
            listing, accepted, skipped, note = self.section_listing(spec)
        else:
            listing = parser.parse(spec["says"], backend=self.backend, client=self.client)
            if "floor" in spec:  # a floor the student set in a follow-up, not in the first message
                listing.floor = spec["floor"]
        agent = StudentAgent(
            name=name,
            listing=listing if listing else Listing(None, []),
            contact=spec.get("contact", ""),
            values=spec.get("values", {}),
            availability=[Block.parse(b) for b in spec.get("free", [])],
            spots=tuple(spec.get("spots", ())),
            reasons=spec.get("reasons", ""),
            answer=scripted(spec.get("answers", {})),
            rng=self.rng,
        )
        self.agents.append(agent)
        self.by_handle[agent.handle] = agent
        if "section" in spec:
            for line in note:
                self.dm(agent, line)
            if listing is None:
                self.dm(agent, "Nothing you asked for fits around your other classes and preferences, so I won't "
                               "post anything. Nothing about you was shared.")
                return
        else:
            self.reply(agent, spec["says"])
            self.dm(agent, f"I read that with the {listing.parser} parser as: you have "
                           f"{listing.have.label() if listing.have else 'nothing to give'}; you want "
                           f"{'; '.join(w.label() for w in listing.wants) or 'nothing back (a giveaway)'}.")
            if listing.have:
                self.dm(agent, f"Rough value of yours: ${listing.have.est_value} (a hint from a table, not a price).")
            if listing.floor is not None:
                self.dm(agent, f"Your floor for it is ${listing.floor:g}. That stays with me: the exchange only ever "
                               "hears yes or no.")
        self.dm(agent, f"I'm posting your listing under the random handle {agent.handle}: the items, their condition "
                       "and what you'd take. Not your name, contact, floor or reasons.")
        self.wire(agent.handle, "exchange", "offer", agent.offer().payload())
        self.exchange.add(agent.offer().payload())
        self.near_misses(agent.handle)
        self.match()

    def near_misses(self, handle: str) -> None:
        """Say which listings fail the public compatibility check against the new one, and why."""
        new = self.exchange.book[handle]
        for other in self.exchange.open_offers():
            for receiver, giver in ((new, other), (other, new)):
                if receiver is giver or giver.have is None or giver.have.category == "section":
                    continue  # section near-misses are just "a different section": not worth a line
                same_kind = [w for w in receiver.wants if w.category == giver.have.category]
                if same_kind and not receiver.wants_item(giver.have):
                    why = same_kind[0].why_not(giver.have)
                    self.note(f"{receiver.handle} wants a {giver.have.category.replace('_', ' ')}, but {giver.handle}'s "
                              f"doesn't fit ({why}). No edge, so nobody is asked.")

    def section_listing(self, spec: dict):
        from swap.agent import StudentAgent as SwapStudent

        sec = spec["section"]
        student = SwapStudent(spec["name"], [self.by_nbr[n] for n in sec["holds"]], [_want(sec["want"])],
                              _prefs(sec.get("prefs", {})), rng=random.Random(0))
        want = student.wants[0]
        listing, accepted, skipped = listing_for(student, want, self.catalog)
        held = student.held(want.course)
        lines = [f"You hold {held.label()} and want another {want.course} section."
                 + (f' You told me: "{spec["reasons"]}"' if spec.get("reasons") else "")]
        lines += [f"Ruled out {s.section.label()}: {s.reason}." for s in skipped]
        if accepted:
            lines.append("You'd accept: " + "; ".join(s.label() for s in accepted) + ".")
        return listing, accepted, skipped, lines

    # ------------------------------------------------------------ matching, with edge questions relayed

    def ask_edge(self, receiver: str, giver: str, gives: bool) -> bool:
        agent = self.by_handle[receiver]
        item = self.exchange.book[giver].have
        payload = {"from": giver, "item": item.payload(), "you_give": gives}
        self.wire("exchange", receiver, "edge?", payload)
        yes = agent.accepts(Item.from_payload(payload["item"]), payload["you_give"])
        decision = agent.decisions[-1][1]
        trade_for = f"your {agent.listing.have.label()}" if gives else "nothing"
        self.dm(agent, f"The exchange asked if you'd take {giver}'s {item.label()} for {trade_for}. "
                       f"I said {'yes' if yes else 'no'}: {decision.why}. It only hears the {'yes' if yes else 'no'}.")
        self.wire(receiver, "exchange", "edge", {"from": giver, "you_give": gives, "answer": "yes" if yes else "no"})
        return yes

    def match(self) -> None:
        new = self.exchange.match(self.ask_edge)
        if not new:
            self.note(f"{len(self.exchange.book)} listing(s) on the book; no loop or chain yet")
            return
        self.note(f"{len(self.exchange.book)} listing(s); found " + ", ".join(f"{t.id}, a {t.loop.describe()}" for t in new))
        self.contention(new)
        queue = list(new)
        while queue:
            trade = queue.pop(0)
            self.result.trades.append(trade)
            self.run_trade(trade)
            self.exchange.close(trade)
            if trade.state is State.UNWOUND:
                self.note(f"{trade.id} unwound; its {len(trade.handles)} listings are back on the book. "
                          "That exact loop won't be proposed again.")
                queue.extend(self.exchange.match(self.ask_edge))

    def contention(self, new: list[Trade]) -> None:
        for trade in new:
            for h in trade.handles:
                item = trade.gives(h)
                if item is None:
                    continue
                for o in self.exchange.open_offers():
                    if o.wants_item(item):
                        self.note(f"{o.handle} also wanted {h}'s {item.label()}, but it went to {trade.id} (each listing "
                                  "joins one trade: most people served, then shorter loops, then first-come), so "
                                  f"{o.handle} stays listed.")

    # ------------------------------------------------------------ one trade

    def members(self, trade: Trade) -> list[StudentAgent]:
        return [self.by_handle[h] for h in trade.handles]

    def broadcast_unwind(self, trade: Trade) -> None:
        for a in self.members(trade):
            self.wire("exchange", a.handle, "unwound", {"trade": trade.id, "reason": trade.reason})
        for a in self.members(trade):
            mine = trade.gives(a.handle)
            keep = f"You keep your {mine.label()}." if mine else "Nothing changed hands for you."
            reason = trade.reason.replace(a.handle, "you") if a.handle in trade.reason else trade.reason
            self.dm(a, f"The trade is off: {reason}. {keep} You're re-listed, and this exact loop won't be "
                       "proposed again.")

    def run_trade(self, trade: Trade) -> None:
        self.say()
        self.say(f"--- {trade.id}: confirmation (1), the loop ---")
        for a in self.members(trade):
            self.wire("exchange", a.handle, "proposal", trade.payload())
        for a in self.members(trade):
            gives, gets = trade.gives(a.handle), trade.gets(a.handle)
            give_line = f"you give your {gives.label()} to {trade.loop.receiver_from(a.handle)}" if gives else (
                "you give nothing")
            get_line = f"you get {trade.loop.giver_to(a.handle)}'s {gets.label()}" if gets else "you get nothing back"
            shape = "a chain that starts with a giveaway" if trade.loop.kind == "chain" else (
                f"a {len(trade.handles)}-way trade loop")
            answer = self.ask(
                a, "loop",
                f"Found {shape}: {give_line}, and {get_line}. The others are only handles to me. One no unwinds "
                "it for everyone. Do you want this trade? (yes/no)",
            )
            self.wire(a.handle, "exchange", "vote", {"trade": trade.id, "handle": a.handle, "answer": answer})
            trade.vote(a.handle, answer == "yes")
            if trade.state is State.UNWOUND:
                self.broadcast_unwind(trade)
                return
        if trade.kind == "countdown":
            self.run_countdown(trade)
        else:
            self.run_meetup(trade)

    def run_meetup(self, trade: Trade) -> None:
        self.say()
        self.say(f"--- {trade.id}: confirmation (2), the meetup plan. The exchange asks yes/no only ---")

        def free(h: str, slot) -> bool:
            self.wire("exchange", h, "free?", {"trade": trade.id, "slot": slot.label()})
            yes = self.by_handle[h].free(slot)
            self.wire(h, "exchange", "free", {"trade": trade.id, "slot": slot.label(), "answer": "yes" if yes else "no"})
            return yes

        def spot_ok(h: str, spot: str) -> bool:
            self.wire("exchange", h, "spot?", {"trade": trade.id, "spot": spot})
            yes = self.by_handle[h].spot_ok(spot)
            self.wire(h, "exchange", "spot", {"trade": trade.id, "spot": spot, "answer": "yes" if yes else "no"})
            return yes

        meetup, why = negotiate(trade.handles, self.window, free, spot_ok)
        if meetup is None:
            trade.unwind(why)
            self.broadcast_unwind(trade)
            return
        trade.propose_plan(meetup)
        for a in self.members(trade):
            self.wire("exchange", a.handle, "meetup", {"trade": trade.id, "slot": meetup.slot.label(), "spot": meetup.spot})
        for a in self.members(trade):
            answer = self.ask(
                a, "meetup",
                f"Meetup plan: {meetup.label()}, a public spot. Bring your item; I'll relay messages there, so nobody "
                "swaps phone numbers. Does this work? (yes/no)",
            )
            self.wire(a.handle, "exchange", "plan-vote", {"trade": trade.id, "handle": a.handle, "answer": answer})
            trade.confirm_plan(a.handle, answer == "yes")
            if trade.state is State.UNWOUND:
                self.broadcast_unwind(trade)
                return

        self.say()
        self.say(f"--- {trade.id}: confirmation (3), the handoff, {meetup.label()} ---")
        for a in self.members(trade):
            if a.ask("handoff", {}) == "noshow":
                self.note(f"{a.handle} hasn't arrived 15 minutes after {format_clock(meetup.slot.start)}")
                self.wire("exchange", a.handle, "no-show", {"trade": trade.id, "handle": a.handle})
                trade.no_show(a.handle)
                self.broadcast_unwind(trade)
                return
        for h in trade.loop.receivers():
            a = self.by_handle[h]
            answer = self.ask(a, "handoff", f"Did you get {trade.loop.giver_to(h)}'s {trade.gets(h).label()}? "
                                            "(got it / didn't get it)")
            self.wire(h, "exchange", "receipt", {"trade": trade.id, "handle": h, "answer": answer})
            trade.receipt(h, answer == "got it")
            if trade.state is State.UNWOUND:
                self.broadcast_unwind(trade)
                return
        for a in self.members(trade):
            self.wire("exchange", a.handle, "done", {"trade": trade.id})
        for a in self.members(trade):
            got = trade.gets(a.handle)
            self.dm(a, (f"Done: you have the {got.label()}." if got else "Done: thanks for giving it away.")
                    + " Your listing is closed; tell me if you want to list something else.")

    def run_countdown(self, trade: Trade) -> None:
        """A section loop: swap's drop/add countdown (with an anchor when every section is full)."""
        have = {h: trade.offers[h].have.attributes["class_nbr"] for h in trade.handles}
        accept = {h: tuple(trade.offers[h].wants[0].attributes["class_nbr"]) for h in trade.handles}
        course = trade.offers[trade.handles[0]].have.attributes["course"]
        proposal = swap_proposal(trade.id, trade.loop, have, accept, course)
        open_seat = self.registrar.open_map()
        anchor = None
        self.say()
        if all(not open_seat[proposal.gets(h)] for h in proposal.handles):
            self.say(f"--- {trade.id}: every target section is full, so someone must drop first (swap engine) ---")
            for a in self.members(trade):
                self.wire("exchange", a.handle, "anchor?", {"trade": trade.id})
                answer = self.ask(
                    a, "anchor",
                    f"Every section in this loop is full. One person drops #{proposal.gives(a.handle)} at T+0 and adds "
                    f"#{proposal.gets(a.handle)} last, about {len(proposal.cycle) * STEP_GAP} s later, holding no seat "
                    "in between. Will you be the anchor? (yes/no)",
                )
                self.wire(a.handle, "exchange", "anchor", {"trade": trade.id, "handle": a.handle, "answer": answer})
                if answer == "yes":
                    anchor = a.handle
                    break
        plan = proposal.make_plan(open_seat, anchor)
        if plan is None:
            trade.unwind(proposal.reason)
            self.broadcast_unwind(trade)
            return
        trade.propose_plan(plan)
        self.say(f"--- {trade.id}: confirmation (2), the countdown plan ---")
        for a in self.members(trade):
            self.wire("exchange", a.handle, "plan", {"trade": trade.id, **plan.payload()})
        for a in self.members(trade):
            lines = [f"  T+{s.at:<2} s  {'YOU' if s.handle == a.handle else s.handle}: {s.describe()}" for s in plan.steps]
            answer = self.ask(a, "plan", "Here's the countdown. Have ASU registration open.\n" + "\n".join(lines)
                              + f"\nRisk: {plan.risk}\nGo ahead? (yes/no)")
            self.wire(a.handle, "exchange", "plan-vote", {"trade": trade.id, "handle": a.handle, "answer": answer})
            proposal.confirm_plan(a.handle, answer == "yes")
            trade.confirm_plan(a.handle, answer == "yes")
            if trade.state is State.UNWOUND:
                self.broadcast_unwind(trade)
                return
        self.say()
        self.say(f"--- {trade.id}: confirmation (3), \"done\" after every countdown step ---")
        t0 = self.t
        for i, step in enumerate(plan.steps, 1):
            self.t = max(self.t, t0 + step.at)
            a = self.by_handle[step.handle]
            self.wire("exchange", a.handle, "go", {"trade": trade.id, "step": i})
            self.dm(a, f"T+{step.at} s. Now: {step.describe()}. Tell me when it's done.")
            ok, why = self.registrar.apply(step)
            self.t += 3
            status = a.ask("step", {"result": "done" if ok else "failed"})
            if status == "timeout":
                self.t = t0 + step.at + STEP_TIMEOUT
                proposal.report(a.handle, "timed out")
            else:
                self.reply(a, status + (f" ({why})" if why else ""))
                self.wire(a.handle, "exchange", "step", {"trade": trade.id, "handle": a.handle, "step": i, "status": status})
                proposal.report(a.handle, status)
            if proposal.state is not SwapState.RUNNING:
                break
        if proposal.state is SwapState.DONE:
            for h in trade.loop.receivers():
                trade.receipt(h, True)
            for a in self.members(trade):
                self.wire("exchange", a.handle, "done", {"trade": trade.id})
                self.dm(a, f"Done. You're in {self.by_nbr[proposal.gets(a.handle)].label()}. Check your ASU schedule.")
            return
        trade.unwind(proposal.reason)
        recovery = proposal.recovery(self.registrar.open_map())
        for a in self.members(trade):
            self.wire("exchange", a.handle, "unwound", {"trade": trade.id, "reason": trade.reason})
            self.dm(a, f"Stop: the countdown broke. {recovery[a.handle]}")


def run(path: str | Path, out: TextIO = sys.stdout, backend: str = "rules") -> Result:
    scenario = json.loads(Path(path).read_text())
    return Sim(scenario, out, backend).run()


def main(argv: list[str]) -> int:
    backend = "rules"
    if argv[:1] == ["--claude"]:
        backend, argv = "auto", argv[1:]
    if len(argv) != 1:
        print("usage: python -m barter.sim [--claude] scenarios/<name>.json", file=sys.stderr)
        return 2
    run(argv[0], backend=backend)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
