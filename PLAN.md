# Barter Bros — plan (Cubbie Hackathon, Track 2: Agent-to-Agent Coordination)

Team hercules · 2026-10-24. Supersedes the Section Swap plan (same repo, renamed). Section swaps stay in
as one item type.

## The moment

"I have a BIO 181 textbook I'll never open again. I need a USB-C charger." Nobody who has a charger wants
a textbook. But Sam has a phone case and needs a textbook, and Priya has a charger and needs a phone case.
Three people, one trade loop, and none of them would ever have found it.

## What it does

You tell your agent, in plain words, over **Discord** (or **iMessage**, planned) what you have and what you want.
Agents meet in an exchange and find **trade loops** (2, 3 or 4 people) and **chains** (someone giving
things away at move-out starts a chain). They agree a meetup that works for everyone. Everyone confirms
before anything happens.

Item types:
- **Things**: books, chargers, cases, calculators, furniture, clothes. A meetup handoff.
- **Class sections**: ASU sections from the public class-search API. The synchronized drop/add
  countdown from the Section Swap build.

## The three things Track 2 judges look for

| Judged on | In Barter Bros |
|---|---|
| **Conflicting constraints** | Fairness: each person privately sets what they'd give up and the least they'd accept in return. A $90 textbook for a $15 charger is a no unless the loop balances for them. Item condition, edition or compatibility (USB-C vs Lightning). Meetup times and places. Several people wanting one item. Loops up to 4. |
| **What they share** | A random handle and item listings (category, attributes, condition). Never the name, the contact info, the private value floor or the reasons. Contact info is never shared: the bot relays meetup messages. |
| **When people confirm** | (1) the trade loop, (2) the meetup plan (a public campus spot and a time that works for all), (3) "got it" at handoff. Any no, or a no-show, unwinds the loop and re-lists everyone. |

## Architecture (Python 3.12)

```text
barter-bros/
├── barter/
│   ├── models.py      Listing (have/want), Item (category, attributes, condition, est_value), Person (private)
│   ├── parse.py       Free text -> Listing. Claude (claude-opus-5-5, structured output) with a rule-based fallback
│   ├── agent.py       Private state; fairness floor; availability; answers proposals
│   ├── match.py       Compatibility (want spec vs item), loop and chain search, fairness check, selection
│   ├── meetup.py      Common time window + public campus spot, by negotiation over availability
│   ├── protocol.py    Lifecycle: proposed → accepted → meetup agreed → handed off | unwound
│   ├── sections.py    The Section Swap code: ASU sections as items + the countdown plan
│   ├── sim.py         Scenario runner (no accounts)
│   └── platforms/
│       ├── discord_bot.py   DMs per user; buttons for yes / no / got it
│       └── imessage.py      (planned, not built) Mac bridge: chat.db in, Messages (AppleScript) out
├── scenarios/*.json
└── tests/
```

### Matching

- Edge A → B when B has an item that satisfies one of A's wants (category + attributes + condition) and the
  trade is worth it to A.
- Search: cycles of length 2–4, plus chains that start at a giveaway.
- Selection: maximize the number of people who get something, then prefer shorter loops, then earlier
  requests.
- Fairness: an edge exists only if A's value for what they get ≥ A's floor for what they give. Values stay
  inside each agent; only a yes or no about the edge leaves it.

### The AI part, and its limits

- `parse.py` turns "need a usb c charger, fast charging if possible" into a structured want. It uses the
  Anthropic API (`claude-opus-5-5`, low effort, structured JSON output) with the server-side fallback enabled.
- With no API key or on any error, a rule-based parser handles the same request. Matching, fairness and
  meetups are plain code, so they are testable and nothing invents a trade.
- The key lives in the owner's Keychain and is read at runtime; never in the repo.

## Platforms

- **Discord:** one bot (the "hercules" app), DM commands and buttons. Token from the Keychain.
- **iMessage (planned, not built):** the Mac bridge would watch chosen conversations in `~/Library/Messages/chat.db` (read-only) and
  reply through the Messages app. It would send only to people who texted it first, and only replies; no
  cold messages.
- Both feed the same agent and exchange. A trade loop can mix a Discord user and an iMessage user.

## Phases

1. **Generalize the engine:** items, the parser with fallback, fairness, loops and chains, meetups. Keep
   sections working. Scenarios: the book/charger/case 3-way, a chain from a move-out giveaway, an unfair
   loop refused, a no-show unwinding, and a mixed loop (one section swap still works). Tests offline.
2. **Platforms:** the Discord bot, tested against fakes (done; never connected live). The iMessage bridge is
   planned and not built. No live sends during the build.
3. **Live test:** the owner and teammates on Discord and iMessage. Then the demo video and the submission.

## Not doing

Payments, shipping, anything off campus, or touching ASU registration.
