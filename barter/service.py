"""The live exchange: agent sessions that stay up, unlike `sim.py`'s scripted run.

A session is one student: an id (a secret token, used only by their own client), a display name
(private, never leaves this module), a channel badge (web, discord, imessage) and their agent.
Every channel calls the same methods:

    post_message(session, text)        free text to your agent; it parses, posts, matches
    vote(session, trade, yes)          (1) the loop
    answer_meetup(session, trade, yes) (2) the meetup plan
    receipt(session, trade, got_it)    (3) "got it" at the handoff
    session_view(session)              everything your agent knows, for you only
    updates(cursor)                    what changed since a cursor, for SSE or a chat adapter

The web server (`barter/server.py`) is one caller. A Discord or iMessage adapter would create a
session with its channel badge, pass the student's messages to `post_message`, map buttons or
replies to the three confirmations, and relay the session's agent messages back from `updates`.

Two logs, kept apart:
- the wire: `self.exchange.log`, written only through `Exchange.transmit`. Every message that
  crosses between agents and the exchange: offers, edge questions and yes/no, proposals, votes,
  free?/spot? questions, the meetup, receipts, done/unwound. It is public, so it carries handles,
  listings, a public slot or spot, and yes/no. Nothing else.
- each session's chat: what an agent tells its own student, which may mention their floor and
  their private values. It is only ever returned to that session.

Bots are sessions too. They answer as their scenario scripts them (`sim.scripted`), after
`delay` seconds on a timer, so a person in the web app watches the ticks arrive. With
`delay=None` (the tests) a bot answers at once on the caller's thread.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from swap.models import parse_clock

from . import parse as parser
from .agent import StudentAgent
from .market import MARKET
from .meetup import Block, Window, negotiate
from .models import Item, Listing, Want
from .protocol import Exchange, State, Trade
from .sim import scripted

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"
CHANNELS = ("web", "discord", "imessage")
BOT_CHANNELS = ("discord", "imessage", "discord", "web", "imessage")  # a mix, for the badges
WINDOW = Window(("SA",), parse_clock("10:00 AM"), parse_clock("4:00 PM"))
GOT_IT, DIDNT = "got it", "didn't get it"


class ServiceError(Exception):
    """A request the service refuses. `status` is the HTTP status the web server maps it to."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Session:
    id: str
    name: str  # private: never in a view, an event or the wire
    channel: str
    agent: StudentAgent
    bot: bool = False
    posted: bool = False  # has put an offer on the book at least once
    chat: list[dict] = field(default_factory=list)  # {"from": "you" | "agent", "text", "t"}

    @property
    def handle(self) -> str:
        return self.agent.handle


def item_view(item: Item | None) -> dict | None:
    """The public listing of an item, plus a readable label."""
    if item is None:
        return None
    return {**item.payload(), "label": item.label()}


def want_view(want: Want) -> dict:
    attrs = {k: list(v) if isinstance(v, tuple) else v for k, v in want.attributes.items()}
    return {"category": want.category, "attributes": attrs, "min_condition": want.min_condition, "label": want.label()}


class ExchangeService:
    def __init__(self, backend: str = "rules", client=None, delay: float | None = None,
                 window: Window = WINDOW, clock: Callable[[], float] = time.time):
        self.backend = backend  # "rules", or "claude" with a client (the server's --claude)
        self.client = client
        self.delay = delay
        self.window = window
        self.clock = clock
        self.lock = threading.RLock()
        self._timers: list[threading.Timer] = []
        self.generation = 0
        self.reset()

    # ------------------------------------------------------------ sessions

    def reset(self) -> None:
        """Empty exchange, no sessions. Pending bot answers from before are dropped."""
        with self.lock:
            for timer in self._timers:
                timer.cancel()
            self._timers = []
            self.generation += 1
            self.exchange = Exchange()
            self.sessions: dict[str, Session] = {}
            self.by_handle: dict[str, Session] = {}
            self.market_seeded = False

    def create_session(self, name: str = "Guest", channel: str = "web", *, bot: bool = False,
                       values: dict | None = None, free: list[str] | None = None, spots: tuple[str, ...] = (),
                       reasons: str = "", contact: str = "", answers: dict | None = None) -> Session:
        """A new student and their agent. With no `free` blocks, the agent is free all through the
        meetup window; with no `spots`, any public spot works."""
        if channel not in CHANNELS:
            raise ServiceError(f"channel must be one of {', '.join(CHANNELS)}")
        with self.lock:
            availability = ([Block.parse(b) for b in free] if free is not None
                            else [Block(d, self.window.start, self.window.end) for d in self.window.days])
            while True:
                agent = StudentAgent(name=name, listing=Listing(None, []), contact=contact, values=dict(values or {}),
                                     availability=availability, spots=tuple(spots), reasons=reasons,
                                     answer=scripted(answers or {}))
                if agent.handle not in self.by_handle:
                    break
            session = Session(secrets.token_urlsafe(12), name, channel, agent, bot=bot)
            self.sessions[session.id] = session
            self.by_handle[agent.handle] = session
            return session

    def session(self, session_id: str) -> Session:
        s = self.sessions.get(session_id)
        if s is None:
            raise ServiceError("no such session", 404)
        return s

    def _say(self, s: Session, text: str, sender: str = "agent") -> None:
        if not s.bot:
            s.chat.append({"from": sender, "text": text, "t": int(self.clock())})

    def _wire(self, sender: str, recipient: str, kind: str, payload: dict) -> dict:
        return self.exchange.transmit(int(self.clock()), sender, recipient, kind, payload).payload

    # ------------------------------------------------------------ talking to your agent

    def post_message(self, session_id: str, text: str) -> None:
        """Free text to your agent. It reads it, says what it understood, and (re)posts your listing."""
        text = text.strip()
        if not text:
            raise ServiceError("say something to your agent")
        with self.lock:
            s = self.session(session_id)
            self._say(s, text, "you")
            live = self._live_trade(s.handle)
            if live is not None:
                self._say(s, f"You're in trade {live.id} right now, so I can't change your listing. Decline it, or "
                             "finish the handoff, and then tell me.")
                return
            listing = parser.parse(text, backend=self.backend, client=self.client)
            if listing.have is None and not listing.wants:
                if listing.floor is not None and s.agent.listing.have is not None:
                    s.agent.listing.floor = listing.floor
                    self._say(s, f"Got it: your floor for your {s.agent.listing.have.label()} is now "
                                 f"${listing.floor:g}. That stays with me; the exchange only ever hears yes or no.")
                    self._forget(s.handle)
                    self._match()
                    return
                self._say(s, "I couldn't find something you have or something you want in that. Try: \"I have a "
                             "TI-84, I need a desk lamp, I'd take anything worth at least $20.\"")
                return
            if listing.floor is None and listing.have is not None and s.agent.listing.floor is not None:
                listing.floor = s.agent.listing.floor  # a new message about the same listing keeps the floor
            self._post(s, listing)

    def _post(self, s: Session, listing: Listing) -> None:
        s.agent.listing = listing
        have = listing.have.label() if listing.have else "nothing to give"
        wants = "; ".join(w.label() for w in listing.wants) or "nothing back (a giveaway)"
        self._say(s, f"I read that with the {listing.parser} parser as: you have {have}; you want {wants}.")
        if listing.have:
            self._say(s, f"Rough value of yours: ${listing.have.est_value} (a hint from a table, not a price).")
        if listing.floor is not None:
            self._say(s, f"Your floor for it is ${listing.floor:g}. That stays with me: the exchange only ever hears "
                         "yes or no.")
        elif listing.have and listing.wants:
            self._say(s, "No floor set, so I'll say yes to anything that matches what you asked for. Tell me "
                         "\"at least $20\" to set one.")
        self._say(s, f"Posting your listing under the random handle {s.handle}: the items, their condition and what "
                     "you'd take. Not your name, contact, floor or reasons.")
        self._wire(s.handle, "exchange", "offer", s.agent.offer().payload())
        self.exchange.add(s.agent.offer().payload())
        s.posted = True
        self._forget(s.handle)
        self._match()

    def _forget(self, handle: str) -> None:
        """Drop cached edge answers involving a listing that changed, so its agent is asked again."""
        for key in [k for k in self.exchange.answers if handle in k[:2]]:
            del self.exchange.answers[key]

    def _live_trade(self, handle: str) -> Trade | None:
        return next((t for t in self.exchange.trades.values()
                     if handle in t.handles and t.state in (State.PROPOSED, State.ACCEPTED_BY_ALL,
                                                            State.MEETUP_AGREED)), None)

    # ------------------------------------------------------------ matching

    def _ask_edge(self, receiver: str, giver: str, gives: bool) -> bool:
        s = self.by_handle[receiver]
        item = self.exchange.book[giver].have
        payload = self._wire("exchange", receiver, "edge?", {"from": giver, "item": item.payload(), "you_give": gives})
        yes = s.agent.accepts(Item.from_payload(payload["item"]), payload["you_give"])
        why = s.agent.decisions[-1][1].why
        mine = f"your {s.agent.listing.have.label()}" if gives else "nothing"
        self._say(s, f"The exchange asked if you'd take {giver}'s {item.label()} for {mine}. I said "
                     f"{'yes' if yes else 'no'}: {why}. It only hears the {'yes' if yes else 'no'}.")
        self._wire(receiver, "exchange", "edge", {"from": giver, "you_give": gives, "answer": "yes" if yes else "no"})
        return yes

    def _match(self) -> None:
        for trade in self.exchange.match(self._ask_edge):
            self._propose(trade)

    def _members(self, trade: Trade) -> list[Session]:
        return [self.by_handle[h] for h in trade.handles]

    def _propose(self, trade: Trade) -> None:
        for s in self._members(trade):
            self._wire("exchange", s.handle, "proposal", trade.payload())
        for s in self._members(trade):
            gives, gets = trade.gives(s.handle), trade.gets(s.handle)
            give = f"you give your {gives.label()} to {trade.loop.receiver_from(s.handle)}" if gives else "you give nothing"
            get = f"you get {trade.loop.giver_to(s.handle)}'s {gets.label()}" if gets else "you get nothing back"
            shape = "a chain that starts with a giveaway" if trade.loop.kind == "chain" else (
                f"a {len(trade.handles)}-way trade loop")
            self._say(s, f"Found {shape}: {give}, and {get}. The others are only handles to me. One no unwinds it "
                         "for everyone. Accept or decline on the Trades tab.")
        self._bots(trade, "loop")

    # ------------------------------------------------------------ bots

    def _bots(self, trade: Trade, kind: str) -> None:
        """Every bot in the trade answers `kind`, one after another."""
        bots = [s.handle for s in self._members(trade) if s.bot]
        self._timers = [t for t in self._timers if t.is_alive()]
        for i, handle in enumerate(bots, 1):
            if self.delay is None:
                self._bot_answer(trade.id, handle, kind, self.generation)
            else:
                timer = threading.Timer(self.delay * i, self._bot_answer, (trade.id, handle, kind, self.generation))
                timer.daemon = True
                self._timers.append(timer)
                timer.start()

    def _bot_answer(self, trade_id: str, handle: str, kind: str, generation: int) -> None:
        with self.lock:
            if generation != self.generation:
                return  # the exchange was reset since this was scheduled
            trade = self.exchange.trades[trade_id]
            agent = self.by_handle[handle].agent
            if kind == "loop" and trade.state is State.PROPOSED and handle not in trade.votes:
                self._vote(trade, handle, agent.ask("loop", {}) == "yes")
            elif kind == "meetup" and trade.state is State.ACCEPTED_BY_ALL and handle not in trade.plan_votes:
                self._plan_vote(trade, handle, agent.ask("meetup", {}) == "yes")
            elif kind == "handoff" and trade.state is State.MEETUP_AGREED and handle not in trade.receipts:
                answer = agent.ask("handoff", {})
                if answer == "noshow":
                    self._wire("exchange", handle, "no-show", {"trade": trade.id, "handle": handle})
                    trade.no_show(handle)
                    self._unwound(trade)
                elif answer in (GOT_IT, DIDNT) and handle in trade.loop.receivers():
                    self._receipt(trade, handle, answer == GOT_IT)
                # anything else ("later"): the bot hasn't been to the meetup yet

    # ------------------------------------------------------------ the three confirmations

    def _trade_for(self, s: Session, trade_id: str) -> Trade:
        trade = self.exchange.trades.get(trade_id)
        if trade is None or s.handle not in trade.handles:
            raise ServiceError("no such trade for you", 404)
        return trade

    def vote(self, session_id: str, trade_id: str, yes: bool) -> None:
        """(1) Accept or decline the loop."""
        with self.lock:
            s = self.session(session_id)
            trade = self._trade_for(s, trade_id)
            if trade.state is not State.PROPOSED or s.handle in trade.votes:
                raise ServiceError(f"{trade.id} isn't waiting for your answer to the loop", 409)
            self._say(s, "Accept." if yes else "Decline.", "you")
            self._vote(trade, s.handle, yes)

    def answer_meetup(self, session_id: str, trade_id: str, yes: bool) -> None:
        """(2) Yes or no to the meetup plan."""
        with self.lock:
            s = self.session(session_id)
            trade = self._trade_for(s, trade_id)
            if trade.state is not State.ACCEPTED_BY_ALL or trade.plan is None or s.handle in trade.plan_votes:
                raise ServiceError(f"{trade.id} isn't waiting for your answer to the meetup", 409)
            self._say(s, "Yes, that meetup works." if yes else "No, that meetup doesn't work.", "you")
            self._plan_vote(trade, s.handle, yes)

    def receipt(self, session_id: str, trade_id: str, got_it: bool = True) -> None:
        """(3) "Got it" (or "didn't get it") at the handoff."""
        with self.lock:
            s = self.session(session_id)
            trade = self._trade_for(s, trade_id)
            if (trade.state is not State.MEETUP_AGREED or s.handle not in trade.loop.receivers()
                    or s.handle in trade.receipts):
                raise ServiceError(f"{trade.id} isn't waiting for your receipt", 409)
            self._say(s, "Got it." if got_it else "I didn't get it.", "you")
            self._receipt(trade, s.handle, got_it)

    def _vote(self, trade: Trade, handle: str, yes: bool) -> None:
        self._wire(handle, "exchange", "vote", {"trade": trade.id, "handle": handle, "answer": "yes" if yes else "no"})
        trade.vote(handle, yes)
        if trade.state is State.UNWOUND:
            self._unwound(trade)
        elif trade.state is State.ACCEPTED_BY_ALL:
            self._meetup(trade)

    def _meetup(self, trade: Trade) -> None:
        def free(h: str, slot) -> bool:
            self._wire("exchange", h, "free?", {"trade": trade.id, "slot": slot.label()})
            yes = self.by_handle[h].agent.free(slot)
            self._wire(h, "exchange", "free", {"trade": trade.id, "slot": slot.label(), "answer": "yes" if yes else "no"})
            return yes

        def spot_ok(h: str, spot: str) -> bool:
            self._wire("exchange", h, "spot?", {"trade": trade.id, "spot": spot})
            yes = self.by_handle[h].agent.spot_ok(spot)
            self._wire(h, "exchange", "spot", {"trade": trade.id, "spot": spot, "answer": "yes" if yes else "no"})
            return yes

        meetup, why = negotiate(trade.handles, self.window, free, spot_ok)
        if meetup is None:
            trade.unwind(why)
            self._unwound(trade)
            return
        trade.propose_plan(meetup)
        for s in self._members(trade):
            self._wire("exchange", s.handle, "meetup", {"trade": trade.id, "slot": meetup.slot.label(), "spot": meetup.spot})
        for s in self._members(trade):
            self._say(s, f"Everyone said yes. Meetup plan: {meetup.label()}, a public spot. I asked your calendar, not "
                         "you, and only said yes or no. I'll relay messages there, so nobody swaps phone numbers. "
                         "Does this work?")
        self._bots(trade, "meetup")

    def _plan_vote(self, trade: Trade, handle: str, yes: bool) -> None:
        self._wire(handle, "exchange", "plan-vote", {"trade": trade.id, "handle": handle, "answer": "yes" if yes else "no"})
        trade.confirm_plan(handle, yes)
        if trade.state is State.UNWOUND:
            self._unwound(trade)
        elif trade.state is State.MEETUP_AGREED:
            for s in self._members(trade):
                got = trade.gets(s.handle)
                self._say(s, f"Meetup agreed by all {len(trade.handles)}: {trade.plan.label()}. Bring your item."
                          + (f" Tap \"Got it\" when you have {trade.loop.giver_to(s.handle)}'s {got.label()}." if got else ""))
            self._bots(trade, "handoff")

    def _receipt(self, trade: Trade, handle: str, got_it: bool) -> None:
        self._wire(handle, "exchange", "receipt", {"trade": trade.id, "handle": handle, "answer": GOT_IT if got_it else DIDNT})
        trade.receipt(handle, got_it)
        if trade.state is State.UNWOUND:
            self._unwound(trade)
        elif trade.state is State.HANDED_OFF:
            for s in self._members(trade):
                self._wire("exchange", s.handle, "done", {"trade": trade.id})
            for s in self._members(trade):
                got = trade.gets(s.handle)
                self._say(s, (f"Done: you have the {got.label()}." if got else "Done: thanks for giving it away.")
                          + " Your listing is closed; tell me if you want to list something else.")
            self.exchange.close(trade)

    def _unwound(self, trade: Trade) -> None:
        for s in self._members(trade):
            self._wire("exchange", s.handle, "unwound", {"trade": trade.id, "reason": trade.reason})
        for s in self._members(trade):
            mine = trade.gives(s.handle)
            keep = f"You keep your {mine.label()}." if mine else "Nothing changed hands for you."
            reason = trade.reason.replace(s.handle, "you")
            self._say(s, f"The trade is off: {reason}. {keep} You're re-listed, and this exact loop won't be proposed "
                         "again.")
        self.exchange.close(trade)
        self._match()

    # ------------------------------------------------------------ demo seeding

    def seed_scenario(self, name: str, human: str | None = None) -> dict:
        """Reset, then load a scenario: one person is the human on the web (the first, or `human` by
        first name); everyone else is a bot that posts their listing now and answers as scripted.
        The human has their scenario's private values, calendar and spots, and no listing yet."""
        path = SCENARIOS / f"{name}.json"
        if "/" in name or not path.is_file():
            raise ServiceError(f"no scenario named {name!r}", 404)
        scenario = json.loads(path.read_text())
        people = scenario.get("people")
        if not people or any("section" in p for p in people):
            raise ServiceError(f"{name} isn't a things-only Barter Bros scenario")
        firsts = [p["name"].split()[0].lower() for p in people]
        me = firsts.index(human.lower()) if human else 0
        if human and human.lower() not in firsts:
            raise ServiceError(f"nobody called {human} in {name}")
        with self.lock:
            self.reset()
            if "window" in scenario:
                w = scenario["window"]
                self.window = Window(tuple(w["days"]), parse_clock(w["start"]), parse_clock(w["end"]))
            else:
                self.window = WINDOW
            spec = people[me]
            you = self.create_session(spec["name"], "web", values=spec.get("values"), free=spec.get("free"),
                                      spots=tuple(spec.get("spots", ())), reasons=spec.get("reasons", ""),
                                      contact=spec.get("contact", ""))
            self._say(you, "Hi. Tell me what you have and what you want, in your own words. I'll find the trade.")
            bots = [p for i, p in enumerate(people) if i != me]
            self._seed_bots(bots)
            return {"session": you.id, "handle": you.handle, "suggested": spec["says"], "bots": len(bots)}

    def seed_market(self) -> dict:
        """Add the twelve market bots (once per reset)."""
        with self.lock:
            if not self.market_seeded:
                self.market_seeded = True
                self._seed_bots(MARKET, offset=1)
            return {"bots": len(MARKET)}

    def _seed_bots(self, specs: list[dict], offset: int = 0) -> None:
        for i, spec in enumerate(specs):
            s = self.create_session(spec["name"], BOT_CHANNELS[(i + offset) % len(BOT_CHANNELS)], bot=True,
                                    values=spec.get("values"), free=spec.get("free"), spots=tuple(spec.get("spots", ())),
                                    reasons=spec.get("reasons", ""), contact=spec.get("contact", ""),
                                    answers=spec.get("answers"))
            listing = parser.parse_rules(spec["says"])  # bots always use the offline rules
            if "floor" in spec:
                listing.floor = spec["floor"]
            self._post(s, listing)

    # ------------------------------------------------------------ views

    def _trade_view(self, trade: Trade, me: str | None) -> dict:
        members = []
        for h in trade.handles:
            members.append({
                "handle": h,
                "channel": self.by_handle[h].channel,
                "you": h == me,
                "gives": item_view(trade.gives(h)),
                "to": trade.loop.receiver_from(h),
                "receiver": trade.loop.giver_to(h) is not None,
                "vote": trade.votes.get(h),
                "plan_vote": trade.plan_votes.get(h),
                "receipt": trade.receipts.get(h),
            })
        plan = trade.plan
        view = {
            "id": trade.id,
            "state": trade.state.value,
            "shape": trade.loop.kind,
            "reason": trade.reason,
            "members": members,
            "plan": {"slot": plan.slot.label(), "spot": plan.spot} if plan is not None else None,
        }
        if me is not None:
            agent = self.by_handle[me].agent
            gets, gives = trade.gets(me), trade.gives(me)
            worth = None
            if gets is not None:
                # Private: what the item is worth to you and your floor. Only in your own session view.
                floor = agent.listing.floor if gives is not None else None
                worth = {"value": agent.value_of(gets), "floor": floor}
            view["mine"] = {"gives": item_view(gives), "gets": item_view(gets), "to": trade.loop.receiver_from(me),
                            "from": trade.loop.giver_to(me), "worth": worth}
        return view

    def session_view(self, session_id: str) -> dict:
        """Everything your agent will show you. Only ever returned to your own session."""
        with self.lock:
            s = self.session(session_id)
            listing = s.agent.listing
            trades = [t for t in self.exchange.trades.values() if s.handle in t.handles]
            return {
                "id": s.id,
                "handle": s.handle,
                "channel": s.channel,
                "chat": list(s.chat),
                "listing": {
                    "have": item_view(listing.have),
                    "est_value": listing.have.est_value if listing.have else None,
                    "wants": [want_view(w) for w in listing.wants],
                    "floor": listing.floor,
                    "parser": listing.parser,
                    "posted": s.posted,
                    "open": s.handle in self.exchange.book and s.handle not in self.exchange.locked,
                },
                "trades": [self._trade_view(t, s.handle) for t in reversed(trades)],
                "generation": self.generation,
            }

    def exchange_view(self) -> dict:
        """The judge's view: anonymous listings and trades, built only from what crossed the wire."""
        with self.lock:
            trades = list(self.exchange.trades.values())
            latest = {}
            for t in trades:
                for h in t.handles:
                    latest[h] = t
            nodes = []
            for s in self.sessions.values():
                if not s.posted:
                    continue
                offer = s.agent.offer()
                t = latest.get(s.handle)
                if s.handle in self.exchange.locked:
                    status = "in_trade"
                elif s.handle in self.exchange.book:
                    status = "listed"
                else:
                    status = "done"
                nodes.append({"handle": s.handle, "channel": s.channel, "have": item_view(offer.have),
                              "wants": [want_view(w)["label"] for w in offer.wants], "status": status,
                              "trade": t.id if t and status != "listed" else None})
            return {
                "generation": self.generation,
                "nodes": nodes,
                "trades": [self._trade_view(t, None) for t in trades],
                "stats": {"agents": len(self.sessions), "listings": len(self.exchange.book), "loops": len(trades)},
            }

    def wire_view(self, after: int = 0) -> list[dict]:
        """The wire log from index `after`: exactly the messages that crossed, as they crossed."""
        with self.lock:
            return [{"i": i, "t": m.t, "sender": m.sender, "recipient": m.recipient, "kind": m.kind,
                     "payload": m.payload} for i, m in enumerate(self.exchange.log[after:], after)]

    def updates(self, cursor: dict, session_id: str | None = None) -> tuple[list[tuple[str, dict]], dict]:
        """What changed since `cursor`, as (event, data) pairs, and the new cursor.

        With a session: a "session" event carrying your view whenever it changes. Without: each new
        wire message as a "wire" event, and an "exchange" event whenever the judge's view changes.
        After a reset: one "reset" event, and the wire replays from the start."""
        with self.lock:
            out: list[tuple[str, dict]] = []
            cursor = dict(cursor)
            if cursor.get("generation") not in (None, self.generation):
                out.append(("reset", {"generation": self.generation}))
                cursor.update(wire=0, digest=None)
            cursor["generation"] = self.generation
            if session_id is not None:
                if session_id not in self.sessions:
                    return out + [("gone", {})], cursor
                view = self.session_view(session_id)
                kind = "session"
            else:
                for msg in self.wire_view(cursor.get("wire", 0)):
                    out.append(("wire", msg))
                cursor["wire"] = len(self.exchange.log)
                view = self.exchange_view()
                kind = "exchange"
            digest = json.dumps(view, sort_keys=True)
            if digest != cursor.get("digest"):
                out.append((kind, view))
                cursor["digest"] = digest
            return out, cursor
