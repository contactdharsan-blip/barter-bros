# Section Swap

Cubbie Hackathon, Track 2 (agent-to-agent coordination). Phase 1: the core and a simulator.

## The moment

You're stuck in MWF morning chem and want Tu/Th. The Tu/Th section is full. Someone in it wants
your section, but neither of you will drop first, because a stranger could take the seat. Sometimes
it takes three people: A wants B's seat, B wants C's, C wants A's, and nobody would find that by
asking around.

## How it works

Each student has a personal agent (`swap/agent.py`). It knows the student's name, whole schedule,
wants and reasons, and keeps all of it. It works out which sections the student would accept, then
posts an offer to the exchange under a random handle.

The exchange (`swap/exchange.py`) draws an edge A → B when B holds a section A would accept, finds
cycles of two and three, and proposes them. The protocol (`swap/protocol.py`) takes a proposal
through `PROPOSED → ACCEPTED_BY_ALL → PLANNED → RUNNING → DONE`, or `CANCELLED` / `FAILED`, and
builds the drop/add plan. Agents never touch registration. Students do every step themselves.

Section data is ASU's public class search (`swap/asu.py`), read from a recorded fixture of
CHM 113 for Spring 2027 (`tests/fixtures/chm113-2271.json`). `asu.fetch()` can call the live API,
but nothing in this repo calls it.

## The three judged points

**1. Conflicting constraints.** The agent drops any section that clashes with the student's other
classes or breaks a time preference ("nothing before 10 AM"), and tells the student why. The
matcher puts each offer in at most one cycle: shorter cycles first, then first-come, then handle
order. If everyone's target is full, nobody's atomic Swap can work, so one student has to drop first.
The plan names that anchor only if they opt in.

**2. What they share.** An offer is exactly `{handle, course, have, accept}`. The exchange rejects
anything else (`Offer.from_payload`). Proposals carry those offers. Votes, plans and step reports
carry handles and class numbers only. Names, other classes and reasons never leave the agent.
`tests/test_privacy.py` runs every scenario and checks every message that crosses the exchange.

**3. When each student confirms.**
(a) The proposed swap. Nothing more is shared either way.
(b) The plan, with its one-line risk and the countdown.
(c) "Done" after each step.
Any "no" cancels the whole cycle. A failed or timed-out step stops the chain, and each student is
told exactly where they stand and what to do.

## Run it

Python 3.12, standard library only.

```sh
python3 -m swap.sim scenarios/chem-3way.json            # 3-way, all full, an anchor opts in
python3 -m swap.sim scenarios/chem-2way-openseat.json   # 2-way, one open seat, no risk
python3 -m swap.sim scenarios/clash.json                # want clashes with BIO 181, nothing offered
python3 -m swap.sim scenarios/decline.json              # one "no" cancels the cycle
python3 -m swap.sim scenarios/chem-3way-stranger.json   # a stranger takes the freed seat; recovery
python3 -m unittest discover -s tests                   # offline
```

Lines starting `agent -> Name` are the agent talking to its own student. `WIRE` lines are the exact
JSON that crosses the exchange. Time is simulated.

## Honest limits

- The students are invented. The sections are real, but the fixture's seat counts leave every
  in-person CHM 113 lecture open. Each scenario that needs full sections sets seat overrides and
  says so in its file and its transcript.
- Lectures only. Labs and recitations are parsed, but no scenario swaps one, and how ASU ties
  them to a lecture is not modelled.
- The anchor's risk is real. A stranger can take a freed seat between steps (see
  `chem-3way-stranger`). Nothing here guarantees a seat.
