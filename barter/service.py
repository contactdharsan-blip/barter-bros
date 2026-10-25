"""The live exchange: agent sessions that stay up, unlike `sim.py`'s scripted run.

Students talk to their agent in a messaging app (Discord, iMessage), never on the website. The web
page is for judges: the live Exchange view and a read-only replay of one student's DM thread. A
chat adapter stays thin, because the whole conversation lives here:

    handle_message(channel, user_ref, text, name=None) -> [reply]   a DM to your agent
    press(channel, user_ref, action_id) -> [reply]                   a button: accept/decline the
                                                                     loop, yes/no to the meetup,
                                                                     got it / didn't get it
    subscribe(listener)                                              replies that come later (when
                                                                     the others answer), pushed as
                                                                     listener(channel, user_ref, reply)

A reply is {"id", "text", "actions": [{"id", "label", "style"}]}, plus "profiles" (a list of
`profiles.PUBLIC_KEYS` dicts) on the one reply that reveals who the partners are. Discord renders
`actions` as buttons (style: primary, secondary, success) and `profiles` as embeds, and sends a
pressed id back to `press`; iMessage can list the labels and map a typed "accept" to the id.
Action ids look like "T1:loop:yes", "T1:meetup:2:no" (the 2nd plan proposed for T1, so a tap on
an older plan's button is answered "That time changed"), "T1:receipt:yes", and "L4:giveaway:yes" for
the one question asked before a listing goes up (an item and no wants, without the word "free" or
the like: give it away, or ask for something?). A press that is no longer open replies "That one's
already settled"; a press on a trade you're not in is refused with `NOT_YOURS` (403). Both create
the session on first contact, keyed by (channel, user_ref); `name` on first contact becomes the
profile's display name.

A DM is read in this order: "help"; "cancel" / "stop" / "remove me" / "never mind" (takes your
listing down); free times ("only free sunday afternoon", "free sat 1-3pm", kept private by your
agent and used for meetups); then, in a trade everyone has accepted, a message the agent relays to
the others under your name; otherwise a listing.

"Can't make it" on a meetup rules that slot out for you, and the agents propose the next slot
everyone is free; the trade unwinds only when no slot in the window is left, and then waits (not
declined forever) until someone's times or listing change.

Demo seeding the bot can reuse: `seed_scenario(name, human=None, channel="discord")` resets and
loads a scenario with that person as the student (user_ref "demo-<first name>"), and
`seed_market()` adds twelve bots.

A session is one student: an id (a secret token; the web page uses it to read `session_view`), a
channel badge, an agent, and a profile. `vote`, `answer_meetup` and `receipt` take a session id
and are what `press` calls; `update_profile(session_id, name, campus, bio)` edits the profile.

What crosses between agents (the wire, `self.exchange.log`, written only through
`Exchange.transmit`): offers, edge questions and yes/no, proposals, votes, an `identity` message
once everyone in a loop has accepted, free?/spot? questions, the meetup, plan-votes (naming the slot
said no to), receipts, done/unwound, `relay` (handle to handle: that a message was passed on, never
its text) and `withdraw` (a handle taking its listing down).
Until a loop is accepted by all, a partner is a handle and an item listing. The `identity` message
carries each other member's public profile (`profiles.PUBLIC_KEYS`), so people know who they're
meeting. Contact info, value floors, private values, calendars and reasons never cross, at any
stage; meetup messages still go through the relay.

Each session's chat is what an agent tells its own student, which may mention their floor. It is
only ever returned to that student.

Bots are sessions too. They answer as their scenario scripts them (`sim.scripted`), after `delay`
seconds on a timer, so a person watching sees the ticks arrive. With `delay=None` (the tests) a
bot answers at once on the caller's thread.
"""

from __future__ import annotations

import json
import re
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
from .meetup import Block, Window, negotiate, parse_availability
from .models import Item, Listing, Want
from .profiles import Profile, demo_profile
from .protocol import Exchange, State, Trade
from .sim import scripted

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"
CHANNELS = ("web", "discord", "imessage")
BOT_CHANNELS = ("discord", "imessage", "discord", "web", "imessage")  # a mix, for the badges
WINDOW = Window(("SA", "SU"), parse_clock("10:00 AM"), parse_clock("4:00 PM"))  # this weekend
GOT_IT, DIDNT = "got it", "didn't get it"
NOT_YOURS = "That button isn't for a trade you're in."
STALE_PLAN = "That time changed; use the newest plan."
HELP = (
    "**Barter Bros** finds trades between students, including 3-way loops nobody would spot alone.\n"
    "1. DM me what you have and what you want, in your own words: \"need a usb-c charger, cheap; "
    "I can give my BIO 181 textbook\".\n"
    "2. I post it under a random handle and ask the other agents. When a trade fits, I DM you an offer: "
    "**Accept** or **Decline**.\n"
    "3. Once everyone accepts, you see who you're trading with, then a meetup time at a public spot. Tell me "
    "when you're free (\"only free Sunday afternoon\") and I'll only say yes to those times. **Can't make it** "
    "moves the meetup to the next time that works for everyone.\n"
    "4. Once everyone has accepted, anything else you DM me goes to the others in your trade. At the handoff, "
    "tap **Got it ✓**.\n"
    "Say \"cancel\" to take your listing down. Nobody sees your name until everyone in a trade says yes, and "
    "nobody ever gets your contact details. `/barter profile` sets your campus and bio; `/barter status` shows "
    "your open trade."
)
_HELP_ASK = re.compile(r"^\s*(?:/?help|\?|what can you do|how does (?:this|it) work)\s*[?.!]*\s*$", re.I)
_WITHDRAW = re.compile(r"^\s*(?:cancel|stop|remove me|never ?mind|nvm|withdraw)"
                       r"(?:\s+(?:my listing|my post|it|that|me|please|everything))?\s*(?:[.!,;].*)?$", re.I | re.S)
LIVE = (State.PROPOSED, State.ACCEPTED_BY_ALL, State.MEETUP_AGREED)

Listener = Callable[[str, str | None, dict], None]


class ServiceError(Exception):
    """A request the service refuses. `status` is the HTTP status the web server maps it to."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Session:
    id: str
    channel: str
    agent: StudentAgent
    profile: Profile
    user_ref: str | None = None  # the chat app's id for this student (Discord user id, iMessage handle)
    bot: bool = False
    posted: bool = False  # has put an offer on the book at least once
    chat: list[dict] = field(default_factory=list)  # {"id", "from": "you"|"agent", "text", "t", "actions"}
    history: list[dict] = field(default_factory=list)  # trades this student finished or that unwound
    times: str = "window"  # where the agent's free times came from: "window" (nothing said), "calendar", "told"
    times_said: str = ""  # what they told me, read back ("Sun afternoon (12:00 PM-5:00 PM)")
    pending: Listing | None = None  # a have-only listing waiting on "give it away?", or on what they'd want
    pending_id: str = ""  # the open giveaway question's id ("L4"), "" once answered

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


def _action(trade: Trade, kind: str, yes: bool, label: str, style: str) -> dict:
    return {"id": f"{trade.id}:{kind}:{'yes' if yes else 'no'}", "label": label, "style": style}


class ExchangeService:
    def __init__(self, backend: str = "rules", client=None, delay: float | None = None,
                 window: Window = WINDOW, clock: Callable[[], float] = time.time):
        self.backend = backend  # "rules", or "claude" with a client (the server's --claude)
        self.client = client
        self.delay = delay
        self.window = window
        self.clock = clock
        self.lock = threading.RLock()
        self.listeners: list[Listener] = []
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
            self.by_user: dict[tuple[str, str], Session] = {}
            self.revealed: set[str] = set()  # trade ids whose members have seen each other's profiles
            self.market_seeded = False

    def create_session(self, name: str = "Guest", channel: str = "web", *, user_ref: str | None = None,
                       bot: bool = False, profile: Profile | None = None, values: dict | None = None,
                       free: list[str] | None = None, spots: tuple[str, ...] = (), reasons: str = "",
                       contact: str = "", answers: dict | None = None) -> Session:
        """A new student and their agent. With no `free` blocks, the agent is free all through the
        meetup window; with no `spots`, any public spot works."""
        if channel not in CHANNELS:
            raise ServiceError(f"channel must be one of {', '.join(CHANNELS)}")
        with self.lock:
            if user_ref is not None and (channel, user_ref) in self.by_user:
                raise ServiceError(f"{channel} user {user_ref} already has a session", 409)
            availability = ([Block.parse(b) for b in free] if free is not None
                            else [Block(d, self.window.start, self.window.end) for d in self.window.days])
            while True:
                agent = StudentAgent(name=name, listing=Listing(None, []), contact=contact, values=dict(values or {}),
                                     availability=availability, spots=tuple(spots), reasons=reasons,
                                     answer=scripted(answers or {}))
                if agent.handle not in self.by_handle:
                    break
            session = Session(secrets.token_urlsafe(12), channel, agent, profile or demo_profile(name),
                              user_ref=user_ref, bot=bot, times="calendar" if free is not None else "window")
            self.sessions[session.id] = session
            self.by_handle[agent.handle] = session
            if user_ref is not None:
                self.by_user[(channel, user_ref)] = session
            return session

    def session(self, session_id: str) -> Session:
        s = self.sessions.get(session_id)
        if s is None:
            raise ServiceError("no such session", 404)
        return s

    def update_profile(self, session_id: str, name: str | None = None, campus: str | None = None,
                       bio: str | None = None) -> None:
        with self.lock:
            p = self.session(session_id).profile
            for key, value in (("name", name), ("campus", campus), ("bio", bio)):
                if value is not None:
                    value = value.strip()[:40 if key != "bio" else 160]
                    if key == "name" and not value:
                        raise ServiceError("a profile needs a name")
                    setattr(p, key, value)

    def _say(self, s: Session, text: str, sender: str = "agent", actions: list[dict] | None = None,
             profiles: list[dict] | None = None) -> None:
        if s.bot:
            return
        entry = {"id": len(s.chat) + 1, "from": sender, "text": text, "t": int(self.clock()), "actions": actions or []}
        if profiles:
            entry["profiles"] = profiles
        s.chat.append(entry)
        if sender == "agent":
            reply = self._reply(entry)
            for listener in list(self.listeners):
                listener(s.channel, s.user_ref, reply)

    def _wire(self, sender: str, recipient: str, kind: str, payload: dict) -> dict:
        return self.exchange.transmit(int(self.clock()), sender, recipient, kind, payload).payload

    # ------------------------------------------------------------ the chat-app API

    def subscribe(self, listener: Listener) -> None:
        """Every agent reply to a student, including the ones that arrive later. Called under the
        service lock, so a listener should queue the reply, not send it inline."""
        with self.lock:
            self.listeners.append(listener)

    def user_session(self, channel: str, user_ref: str, name: str | None = None) -> Session:
        """The session for a chat-app user, created on first contact."""
        with self.lock:
            s = self.by_user.get((channel, user_ref))
            if s is None:
                s = self.create_session(name or "New student", channel, user_ref=user_ref)
                self._say(s, "Hi, I'm your Barter Bros agent. Tell me what you have and what you want, in your own "
                             "words, and I'll find the trade. Nobody sees your name until everyone in a trade says yes.")
            return s

    @staticmethod
    def _reply(entry: dict) -> dict:
        reply = {"id": entry["id"], "text": entry["text"], "actions": entry["actions"]}
        if "profiles" in entry:
            reply["profiles"] = entry["profiles"]
        return reply

    def _replies(self, s: Session, since: int) -> list[dict]:
        return [self._reply(e) for e in s.chat[since:] if e["from"] == "agent"]

    def handle_message(self, channel: str, user_ref: str, text: str, name: str | None = None) -> list[dict]:
        """A DM to your agent from a chat app. Returns the agent's replies to it."""
        with self.lock:
            s = self.user_session(channel, user_ref, name)
            since = len(s.chat)
            self.post_message(s.id, text)
            return self._replies(s, since)

    def press(self, channel: str, user_ref: str, action_id: str) -> list[dict]:
        """A button press (an action id from a reply). Returns the agent's replies to it."""
        with self.lock:
            s = self.by_user.get((channel, user_ref))
            if s is None:
                raise ServiceError("no such user", 404)
            try:
                trade_id, kind, *plan_round, answer = action_id.split(":")
                yes = {"yes": True, "no": False}[answer]
                do = {"loop": self.vote, "meetup": self.answer_meetup, "receipt": self.receipt,
                      "giveaway": self.answer_giveaway}[kind]
                if len(plan_round) > (kind == "meetup"):
                    raise ValueError(action_id)
                extra = {"plan_round": int(plan_round[0]) if plan_round else -1} if kind == "meetup" else {}
            except (ValueError, KeyError) as e:
                raise ServiceError(f"unknown action {action_id!r}") from e
            since = len(s.chat)
            try:
                do(s.id, trade_id, yes, **extra)
            except ServiceError as e:
                if e.status != 409:
                    raise
                self._say(s, "That one's already settled.")
            return self._replies(s, since)

    # ------------------------------------------------------------ talking to your agent

    def post_message(self, session_id: str, text: str) -> None:
        """Free text to your agent. It reads it, says what it understood, and (re)posts your listing."""
        text = text.strip()
        if not text:
            raise ServiceError("say something to your agent")
        with self.lock:
            s = self.session(session_id)
            self._say(s, text, "you")
            if _HELP_ASK.match(text):
                self._say(s, HELP)
                return
            live = self._live_trade(s.handle)
            if _WITHDRAW.match(text):
                self._withdraw(s, live)
                return
            times = parse_availability(text)
            if times is not None:
                self._set_times(s, *times, live)
            if live is not None:
                if times is None and live.id in self.revealed:
                    self._relay(s, live, text)
                elif times is None:
                    self._say(s, f"You're in trade {live.id} right now, so I can't change your listing. Decline it, or "
                                 "finish the handoff, and then tell me. Once everyone has said yes, I pass messages "
                                 "like this on to the others.")
                return
            listing = parser.parse(text, backend=self.backend, client=self.client)
            if s.pending is not None and listing.have is None and listing.wants:
                listing.have = s.pending.have  # "I want something for it", then what they want
                listing.floor = listing.floor if listing.floor is not None else s.pending.floor
            if listing.have is None and not listing.wants:
                if times is not None:  # it was only about when they're free
                    self._match()
                    return
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
            if listing.is_giveaway and not parser.says_giveaway(text):
                self._ask_giveaway(s, listing)
                return
            self._post(s, listing)

    def _ask_giveaway(self, s: Session, listing: Listing) -> None:
        """An item and no wants, and they never said "free": ask before giving it away."""
        s.pending, s.pending_id = listing, f"L{len(s.chat) + 1}"
        self._say(s, f"I read that as: you have {listing.have.label()}, and you didn't say what you'd want for it. "
                     "Should I list it as a giveaway, or is there something you want for it?",
                  actions=[{"id": f"{s.pending_id}:giveaway:yes", "label": "Give it away", "style": "primary"},
                           {"id": f"{s.pending_id}:giveaway:no", "label": "I want something for it",
                            "style": "secondary"}])

    def answer_giveaway(self, session_id: str, ref: str, yes: bool) -> None:
        """The answer to "give it away?": post the giveaway, or wait to hear what they'd want."""
        with self.lock:
            s = self.session(session_id)
            if s.pending is None or not s.pending_id or ref != s.pending_id:
                raise ServiceError("that question isn't open", 409)
            self._say(s, "Give it away" if yes else "I want something for it", "you")
            s.pending_id = ""
            if yes:
                self._post(s, s.pending)
            else:
                self._say(s, f"OK. What would you take for your {s.pending.have.label()}? Tell me, e.g. "
                             "\"I want a desk lamp\".")

    def _withdraw(self, s: Session, live: Trade | None) -> None:
        """'cancel': take the listing down. A trade still waiting for answers is declined first; once
        everyone has accepted, the agent won't pull out on the others."""
        if live is not None and live.state is not State.PROPOSED:
            self._say(s, f"Everyone in {live.id} has said yes, so I can't take your listing down mid-trade. If "
                         "something changed, tell me here and I'll pass it on to the others.")
            return
        s.pending, s.pending_id = None, ""
        if s.handle not in self.exchange.book:
            self._say(s, "You don't have a listing up, so there's nothing to take down. Tell me what you have and "
                         "what you want whenever you're ready.")
            return
        self.exchange.withdraw(s.handle)  # off the book first, so the unwind below can't re-match it
        self._forget(s.handle)
        self._wire(s.handle, "exchange", "withdraw", {"handle": s.handle})
        s.agent.listing, s.posted = Listing(None, []), False
        if live is not None:
            self._vote(live, s.handle, False)
        self._say(s, "Done: your listing is down, and I won't propose any trades for you. Tell me what you have and "
                     "what you want to list again.")

    def _set_times(self, s: Session, blocks: list[Block], said: str, live: Trade | None) -> None:
        """Free times from what the student said. Private: the exchange only ever hears yes or no
        about one slot. A pending meetup plan that no longer fits gets a "no", so the agents move it."""
        s.agent.availability, s.times, s.times_said = blocks, "told", said
        s.agent.refused.clear()  # what they say now replaces what they turned down before
        inside = any(b.covers(slot) for slot in self.window.slots() for b in blocks)
        where = ("That stays with me: the exchange only hears yes or no about one meetup slot at a time." if inside
                 else f"That's outside the meetup window ({self.window.label()}), so I can't offer a meetup then.")
        self._say(s, f"Got it: you're free {said}. {where}")
        self.exchange.reopen(s.handle)
        if live is None or live.plan is None or s.agent.free(live.plan.slot):
            return
        if live.state is State.ACCEPTED_BY_ALL:
            self._say(s, f"{live.plan.slot.label()} isn't in those times, so I said no to it.")
            self._plan_vote(live, s.handle, False)
        else:
            self._say(s, f"Your meetup for {live.id} is already agreed: {live.plan.label()}. If you can't make it, "
                         "tell me here and I'll pass it on to the others.")

    def _relay(self, s: Session, trade: Trade, text: str) -> None:
        """After everyone accepted: a DM goes to the others in the trade under the sender's name. The
        wire hears only that a message passed, never what it said."""
        clean, cut = parser.scrub_contact(text)
        others = [o for o in self._members(trade) if o is not s]
        for o in others:
            self._wire(s.handle, o.handle, "relay", {"trade": trade.id})
            self._say(o, f"{s.profile.name} ({trade.id}): {clean}")
        note = " I left out the contact details: messages go through me." if cut else ""
        self._say(s, f"Passed that on to {', '.join(o.profile.name for o in others)}.{note}")

    def _post(self, s: Session, listing: Listing) -> None:
        s.pending, s.pending_id = None, ""
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
        self.exchange.reopen(handle)

    def _live_trade(self, handle: str) -> Trade | None:
        return next((t for t in self.exchange.trades.values() if handle in t.handles and t.state in LIVE), None)

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
            self._say(s, f"Found {shape}: {give}, and {get}. The others are only handles until everyone says yes. "
                         "One no unwinds it for everyone. Do you want this trade?",
                      actions=[_action(trade, "loop", True, "Accept", "primary"),
                               _action(trade, "loop", False, "Decline", "secondary")])
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
                    self._unwound(trade, no_show=handle)
                elif answer in (GOT_IT, DIDNT) and handle in trade.loop.receivers():
                    self._receipt(trade, handle, answer == GOT_IT)
                # anything else ("later"): the bot hasn't been to the meetup yet

    # ------------------------------------------------------------ the three confirmations

    def _trade_for(self, s: Session, trade_id: str) -> Trade:
        trade = self.exchange.trades.get(trade_id)
        if trade is None:
            raise ServiceError("no such trade", 404)
        if s.handle not in trade.handles:
            raise ServiceError(NOT_YOURS, 403)
        return trade

    def vote(self, session_id: str, trade_id: str, yes: bool) -> None:
        """(1) Accept or decline the loop."""
        with self.lock:
            s = self.session(session_id)
            trade = self._trade_for(s, trade_id)
            if trade.state is not State.PROPOSED or s.handle in trade.votes:
                raise ServiceError(f"{trade.id} isn't waiting for your answer to the loop", 409)
            self._say(s, "Accept" if yes else "Decline", "you")
            self._vote(trade, s.handle, yes)

    def answer_meetup(self, session_id: str, trade_id: str, yes: bool, plan_round: int | None = None) -> None:
        """(2) Yes or no to the meetup plan. A button carries the `plan_round` it was shown with, so
        a tap on an older plan's button never answers a newer time."""
        with self.lock:
            s = self.session(session_id)
            trade = self._trade_for(s, trade_id)
            if trade.state is State.ACCEPTED_BY_ALL and plan_round is not None and plan_round != trade.plan_round:
                self._say(s, STALE_PLAN)
                return
            if trade.state is not State.ACCEPTED_BY_ALL or trade.plan is None or s.handle in trade.plan_votes:
                raise ServiceError(f"{trade.id} isn't waiting for your answer to the meetup", 409)
            self._say(s, "Works for me" if yes else "Can't make it", "you")
            self._plan_vote(trade, s.handle, yes)

    def receipt(self, session_id: str, trade_id: str, got_it: bool = True) -> None:
        """(3) "Got it" (or "didn't get it") at the handoff."""
        with self.lock:
            s = self.session(session_id)
            trade = self._trade_for(s, trade_id)
            if (trade.state is not State.MEETUP_AGREED or s.handle not in trade.loop.receivers()
                    or s.handle in trade.receipts):
                raise ServiceError(f"{trade.id} isn't waiting for your receipt", 409)
            self._say(s, "Got it ✓" if got_it else "Didn't get it", "you")
            self._receipt(trade, s.handle, got_it)

    def _vote(self, trade: Trade, handle: str, yes: bool) -> None:
        self._wire(handle, "exchange", "vote", {"trade": trade.id, "handle": handle, "answer": "yes" if yes else "no"})
        trade.vote(handle, yes)
        if trade.state is State.UNWOUND:
            self._unwound(trade)
        elif trade.state is State.ACCEPTED_BY_ALL:
            self._reveal(trade)
            self._meetup(trade)

    def _reveal(self, trade: Trade) -> None:
        """Everyone said yes: each member now sees who the others are. Public profile fields only."""
        members = self._members(trade)
        for s in members:
            others = [o.profile.public(o.handle) for o in members if o is not s]
            self._wire("exchange", s.handle, "identity", {"trade": trade.id, "profiles": others})
        self.revealed.add(trade.id)
        for s in members:
            others = [o for o in members if o is not s]
            names = ", ".join(f"{o.profile.name} ({o.handle})" for o in others)
            self._say(s, f"Everyone said yes, so now you can see who you're trading with: {names}. Contact details "
                         "still aren't shared; I relay messages.", profiles=[o.profile.public(o.handle) for o in others])

    def _times_note(self, s: Session) -> str:
        """What the agent used to answer "free then?", in the student's terms."""
        if s.times == "calendar":
            return "I asked your calendar, not you, and only said yes or no."
        if s.times == "told":
            return f"I checked it against the times you gave me ({s.times_said}) and only said yes or no."
        return (f"You haven't told me when you're free, so I said yes to any time in the window "
                f"({self.window.label()}). Tell me (\"only free Sunday afternoon\") if that's wrong.")

    def _meetup(self, trade: Trade, moved: tuple[str, object] | None = None) -> None:
        """Ask for the earliest slot and spot everyone says yes to. `moved` is (handle, slot) when
        someone just said they can't make the last plan."""
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
            trade.unwind(why, stalled=True)
            self._unwound(trade)
            return
        trade.propose_plan(meetup)
        for s in self._members(trade):
            self._wire("exchange", s.handle, "meetup", {"trade": trade.id, "slot": meetup.slot.label(), "spot": meetup.spot})
        for s in self._members(trade):
            lead = ""
            if moved is not None:
                who = "You" if moved[0] == s.handle else self.by_handle[moved[0]].profile.name
                lead = f"{who} can't make {moved[1].label()}, so the agents found the next time everyone is free. "
            self._say(s, f"{lead}Meetup plan: {meetup.label()}, a public spot. {self._times_note(s)} I relay "
                         "messages, so nobody swaps phone numbers. Does this work?",
                      actions=[_action(trade, f"meetup:{trade.plan_round}", True, "Works for me", "primary"),
                               _action(trade, f"meetup:{trade.plan_round}", False, "Can't make it", "secondary")])
        self._bots(trade, "meetup")

    def _plan_vote(self, trade: Trade, handle: str, yes: bool) -> None:
        """A "no" rules that slot out for this student only, and the agents look for the next one."""
        slot = trade.plan.slot
        self._wire(handle, "exchange", "plan-vote",
                   {"trade": trade.id, "handle": handle, "answer": "yes" if yes else "no", "slot": slot.label()})
        if not yes:
            self.by_handle[handle].agent.refused.add(slot)
            trade.reject_plan(handle)
            self._meetup(trade, moved=(handle, slot))
            return
        trade.confirm_plan(handle, yes)
        if trade.state is State.MEETUP_AGREED:
            for s in self._members(trade):
                got = trade.gets(s.handle)
                text = f"Meetup agreed by all {len(trade.handles)}: {trade.plan.label()}. Bring your item."
                if got:
                    self._say(s, text + f" Tap \"Got it\" when you have {trade.loop.giver_to(s.handle)}'s {got.label()}.",
                              actions=[_action(trade, "receipt", True, "Got it ✓", "success"),
                                       _action(trade, "receipt", False, "Didn't get it", "secondary")])
                else:
                    self._say(s, text)
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
                s.profile.completed += 1
                s.profile.showed_up += 1
                self._record(s, trade)
                self._say(s, (f"Done: you have the {got.label()}." if got else "Done: thanks for giving it away.")
                          + " Your listing is closed; tell me if you want to list something else.")
            self.exchange.close(trade)

    def _record(self, s: Session, trade: Trade) -> None:
        gives, gets = trade.gives(s.handle), trade.gets(s.handle)
        s.history.append({"trade": trade.id, "state": trade.state.value, "t": int(self.clock()),
                          "gave": gives.label() if gives else None, "got": gets.label() if gets else None,
                          "people": len(trade.handles), "reason": trade.reason})

    def _unwound(self, trade: Trade, no_show: str | None = None) -> None:
        for s in self._members(trade):
            self._wire("exchange", s.handle, "unwound", {"trade": trade.id, "reason": trade.reason})
        for s in self._members(trade):
            if no_show is not None:
                if s.handle == no_show:
                    s.profile.no_shows += 1
                else:
                    s.profile.showed_up += 1
            self._record(s, trade)
            mine = trade.gives(s.handle)
            keep = f"You keep your {mine.label()}." if mine else "Nothing changed hands for you."
            reason = trade.reason.replace(s.handle, "you")
            if s.handle not in self.exchange.book:
                after = "Your listing is down."
            elif trade.stalled:
                after = ("You're re-listed. I'll try this trade again if someone's free times or listing change: tell "
                         "me when you're free (\"free sat 1-3pm\").")
            else:
                after = "You're re-listed, and this exact loop won't be proposed again."
            self._say(s, f"The trade is off: {reason}. {keep} {after}")
        self.exchange.close(trade)
        self._match()

    # ------------------------------------------------------------ demo seeding

    def seed_scenario(self, name: str, human: str | None = None, channel: str = "discord") -> dict:
        """Reset, then load a scenario. One person (the first, or `human` by first name) is the
        student being demoed: they DM their agent over `channel` with their scripted message, and
        a web companion can follow along by session id. Everyone else is a bot that posts their
        listing first and answers as scripted. The student keeps their scenario's private values,
        calendar and spots."""
        path = SCENARIOS / f"{name}.json"
        if "/" in name or not path.is_file():
            raise ServiceError(f"no scenario named {name!r}", 404)
        scenario = json.loads(path.read_text())
        people = scenario.get("people")
        if not people or any("section" in p for p in people):
            raise ServiceError(f"{name} isn't a things-only Barter Bros scenario")
        firsts = [p["name"].split()[0].lower() for p in people]
        if human and human.lower() not in firsts:
            raise ServiceError(f"nobody called {human} in {name}")
        me = firsts.index(human.lower()) if human else 0
        with self.lock:
            self.reset()
            w = scenario.get("window")
            self.window = Window(tuple(w["days"]), parse_clock(w["start"]), parse_clock(w["end"])) if w else WINDOW
            spec = people[me]
            user_ref = f"demo-{firsts[me]}"
            you = self.create_session(spec["name"], channel, user_ref=user_ref, values=spec.get("values"),
                                      free=spec.get("free"), spots=tuple(spec.get("spots", ())),
                                      reasons=spec.get("reasons", ""), contact=spec.get("contact", ""))
            self._say(you, "Hi, I'm your Barter Bros agent. Tell me what you have and what you want, in your own "
                           "words, and I'll find the trade. Nobody sees your name until everyone in a trade says yes.")
            bots = [p for i, p in enumerate(people) if i != me]
            self._seed_bots(bots)
            self.handle_message(channel, user_ref, spec["says"])
            return {"session": you.id, "handle": you.handle, "channel": channel, "user_ref": user_ref,
                    "bots": len(bots)}

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

    def _listing_now(self, s: Session) -> dict:
        offer = s.agent.offer()
        return {"have": item_view(offer.have), "wants": [want_view(w)["label"] for w in offer.wants],
                "open": s.handle in self.exchange.book}

    def _can_see(self, viewer: str, handle: str) -> bool:
        return viewer == handle or any(
            t.id in self.revealed and viewer in t.handles and handle in t.handles for t in self.exchange.trades.values())

    def profile_view(self, session_id: str, handle: str | None = None) -> dict:
        """Your own profile, or a trade partner's once everyone in a loop you share has accepted."""
        with self.lock:
            s = self.session(session_id)
            target = self.by_handle.get(handle or s.handle)
            if target is None or not self._can_see(s.handle, target.handle):
                raise ServiceError("profiles are shown only after everyone in your loop accepts", 403)
            view = {**target.profile.public(target.handle), "channel": target.channel,
                    "listing": self._listing_now(target), "you": target is s}
            if target is s:
                view["history"] = list(reversed(s.history))
            return view

    def _trade_view(self, trade: Trade, me: str | None) -> dict:
        revealed = me is not None and trade.id in self.revealed
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
                "profile": self.by_handle[h].profile.public(h) if revealed and h != me else None,
            })
        plan = trade.plan
        view = {
            "id": trade.id,
            "state": trade.state.value,
            "shape": trade.loop.kind,
            "reason": trade.reason,
            "revealed": revealed,
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

    def _action_open(self, s: Session, action: dict) -> bool:
        trade_id, kind, *plan_round, _ = action["id"].split(":")
        if kind == "giveaway":
            return trade_id == s.pending_id
        t = self.exchange.trades.get(trade_id)
        if t is None:
            return False
        if kind == "loop":
            return t.state is State.PROPOSED and s.handle not in t.votes
        if kind == "meetup":
            return (t.state is State.ACCEPTED_BY_ALL and s.handle not in t.plan_votes
                    and plan_round == [str(t.plan_round)])
        return t.state is State.MEETUP_AGREED and s.handle not in t.receipts

    def session_view(self, session_id: str) -> dict:
        """Everything your agent will show you. Only ever returned to your own session."""
        with self.lock:
            s = self.session(session_id)
            listing = s.agent.listing
            trades = [t for t in self.exchange.trades.values() if s.handle in t.handles]
            chat = [{**e, "actions": [{**a, "open": self._action_open(s, a)} for a in e["actions"]]} for e in s.chat]
            return {
                "id": s.id,
                "handle": s.handle,
                "channel": s.channel,
                "profile": s.profile.public(s.handle),
                "chat": chat,
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
                "history": list(reversed(s.history)),
                "generation": self.generation,
            }

    def exchange_view(self) -> dict:
        """The judge's view: anonymous listings and trades. Profiles appear only on the wire, in the
        `identity` messages sent once a loop is accepted by all."""
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
