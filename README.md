# Barter Bros

Cubbie Hackathon, Track 2 (agent-to-agent coordination). Phase 1: the engine and a simulator.
The spec is `PLAN.md`. The live service and a web page for judges are in; the Discord and
iMessage adapters come next.

## The moment

"I have a BIO 181 textbook I'll never open again. I need a USB-C charger." Nobody who has a
charger wants a textbook. But Sam has a phone case and needs the textbook, and Priya has a
charger and needs a phone case. Three people, one trade loop, and none of them would have found it.

## How it works

1. **You tell your agent in plain words.** `barter/parse.py` turns the text into a listing: what
   you have (category, attributes such as connector or course, condition) and what you'd take.
   The rule parser always works offline. The Claude parser (`claude-opus-5-5`, structured JSON)
   is used only when asked for and a key is configured, and falls back to the rules on a refusal or
   any error. Each item gets a rough dollar value from a small table, as a hint only.
2. **Your agent posts a listing under a random handle** (`barter/agent.py`). It keeps everything
   else: your name, contact, your floor ("I'd give the textbook for anything worth $30 to me"),
   what things are worth to you, when you're free, where you'll meet, and why.
3. **The exchange finds loops and chains** (`barter/match.py`). It draws an edge when your item
   fits someone's want (it checks that from the public listings), then asks that person's agent
   "would you take this for yours?" and hears only yes or no. It searches cycles of 2 to 4 people
   and chains that start at a giveaway, then picks loops that serve the most people, then shorter
   ones, then the earliest requests.
4. **The agents agree a meetup** (`barter/meetup.py`): the earliest 30-minute slot everyone says
   yes to, and the first public ASU Tempe spot everyone says yes to (Memorial Union, Hayden
   Library, Noble Library, Student Pavilion).
5. **Everyone confirms** (`barter/protocol.py`): `proposed -> accepted_by_all -> meetup_agreed ->
   handed_off`, or `unwound`.

Class sections still work: `barter/sections.py` wraps the Section Swap engine in `swap/`
(unchanged). A seat is an item, and a section loop runs on swap's drop/add countdown.

## The three judged points

**1. Conflicting constraints.** Private fairness floors (an unfair loop is refused even though
everyone wants what they'd get), compatibility (Lightning is not USB-C, a minimum wattage,
an edition, a minimum condition), meetup times and spots, several people wanting one item (each
listing joins one trade), and loops capped at 4.

**2. What they share.** A listing is exactly `{handle, have, wants}`, and an item is exactly
`{category, attributes, condition}`. The exchange rejects anything else. Every other message
is a handle, a trade id, a public slot or spot, and a yes or no. Names, contact info, floors,
private values, calendars and reasons never leave the agent. `tests/test_barter_privacy.py`
checks every message in every scenario.

**3. When people confirm.** (1) the trade loop, (2) the meetup plan, (3) "got it" from each
person who receives something. Any no, a no-show or a "didn't get it" unwinds the loop, re-lists
everyone, and that exact loop is never proposed again.

## Run it

Python 3.12 or later. The engine, simulator and tests use the standard library only.

```sh
python3 -m barter.sim scenarios/book-charger-case-3way.json  # the moment, plus someone who waits
python3 -m barter.sim scenarios/moveout-chain.json           # a move-out giveaway starts a 3-step chain
python3 -m barter.sim scenarios/unfair-refused.json          # a private floor blocks a loop; a fair one wins
python3 -m barter.sim scenarios/no-show-unwind.json          # a no-show unwinds; a new partner is found
python3 -m barter.sim scenarios/no-common-slot.json          # no shared 30 minutes, so no trade
python3 -m barter.sim scenarios/lightning-vs-usbc.json       # an incompatible charger never matches
python3 -m barter.sim scenarios/mixed-with-section.json      # a CHM 113 section 3-way and a thing trade
python3 -m swap.sim scenarios/chem-3way.json                 # the original Section Swap simulator
python3 -m unittest discover -s tests                        # offline; never calls the network
```

`agent -> Name` lines are an agent talking to its own student. `WIRE` lines are the exact JSON
that crosses the exchange. Times are simulated and everyone is invented.

To try the Claude parser: `uv venv && uv pip install anthropic==2.3.0`, then
`.venv/bin/python -m barter.sim --claude scenarios/<name>.json`. The key is read at runtime from
the macOS Keychain (`barter/keychain.py`, account `hercules`, service `anthropic-api-key`) or
from `ANTHROPIC_API_KEY`, and is never printed or written to disk. Without a key it uses the rules.

## The live service and the web page

`barter/service.py` keeps agent sessions up and is what a chat app calls. Students talk to their
agent in Discord or iMessage, never on the website:

- `handle_message(channel, user_ref, text, name=None) -> [reply]`: a DM to your agent.
- `press(channel, user_ref, action_id) -> [reply]`: a button. Ids look like `T1:loop:yes`,
  `T1:meetup:no`, `T1:receipt:yes`.
- `subscribe(listener)`: replies that arrive later, as `listener(channel, user_ref, reply)`.
- A reply is `{"id", "text", "actions": [{"id", "label", "style"}]}`, plus `"profiles"` on the
  reply that reveals who the partners are.
- `seed_scenario(name)` and `seed_market()` load demo bots.

Profiles: until everyone in a loop accepts, partners are handles and item listings. Then an
`identity` message on the wire carries each other member's public profile (name, initials and
colour, campus, bio, member since, trade record). Contact info, value floors and reasons never
cross. `tests/test_service.py` checks both sides of that.

```sh
uv pip install --python .venv/bin/python -e '.[web]'
cd web && npm install && npm run build && cd ..
.venv/bin/python -m barter.server --port 8787   # then open http://127.0.0.1:8787
node scripts/smoke_web.mjs                       # WebKit only; screenshots in out/screens/
```

The page shows the live Exchange (trades as rings, waiting listings, the wire log with an
"identity revealed" filter) and a read-only, Discord-styled replay of Jordan's DMs. A "demo
driver" under the replay presses Jordan's open button through the same chat-adapter endpoint a
Discord client would use. The chat adapters can also run out of process through
`POST /api/channels/{channel}/{user_ref}/messages` and `.../press`; the server binds to
127.0.0.1 and has no authentication.

## Honest limits

- The rule parser knows the categories in `barter/models.py` and common phrasings. Anything
  else becomes `other` with a short kind, which only matches the same words.
- One listing per person: one item to give, any number of wants.
- A chain's last person receives and gives nothing. If they listed an item, it is not part of
  the trade, and their listing closes when they get what they wanted.
- Values are self-reported, and the table is rough. Nothing checks condition at the handoff
  except the receiver saying "got it".
- The Claude backend is tested only against a mocked client. No live call has been made from
  this repo.
