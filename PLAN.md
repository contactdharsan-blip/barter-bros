# Section Swap — plan (Cubbie Hackathon, Track 2: Agent-to-Agent Coordination)

Team hercules · written 2026-10-24.

## The moment

"I got MWF 8 AM chem and would trade my soul for TTh." Somewhere on campus, someone in the TTh section
wants MWF. Both sections are full, so neither can just switch, and neither will drop first in case a
stranger takes the seat.

## What it does

Each student has a personal agent. Agents meet in an exchange and find swaps (two-way, and three-way
cycles nobody would find by asking around). They agree on a **synchronized drop/add plan** and walk each
student through it on a shared countdown. Agents never touch registration; students do every step.

The three things Track 2 judges look for:

| Judged on | How Section Swap shows it |
|---|---|
| **Conflicting constraints** | Each student's other classes (no time clashes), time preferences ("nothing before 10 AM"), several students wanting the same section, and cycles longer than two. Settled by deterministic matching: shortest cycles first, first-come priority, each agent in one cycle at most |
| **What they share** | Only `course · section I hold · sections I'd accept`, under a random handle. Never the name, the other classes, or the reasons. A test asserts the exchange payload has no other fields |
| **When students confirm** | (1) every proposed swap, before anything is shared beyond the handle; (2) the execution plan, including which step carries risk; (3) "done" after each step. Any "no" cancels the whole cycle |

## Architecture (Python 3.12, stdlib only for the core)

```text
section-swap/
├── swap/
│   ├── models.py      Section, Meeting (days, start, end), Enrollment, Want
│   ├── asu.py         ASU class-search reader (public API, Bearer null) + fixture loader
│   ├── agent.py       StudentAgent: private state; computes acceptable sections that don't clash
│   ├── exchange.py    Offer book + matcher (cycles of length 2–3, priority, one cycle per agent)
│   ├── protocol.py    Proposal lifecycle and the execution plan (order of steps, risk, countdown)
│   ├── sim.py         Scenario runner: plays a scripted semester through agents + exchange
│   └── discord_bot.py (phase 3) DMs per student; one bot hosts many agents
├── scenarios/*.yaml   Invented students; real ASU section data from fixtures
├── tests/             Offline only; fixtures recorded from the ASU API
└── README.md
```

### Matching

Directed graph: an edge A → B when B holds a section A would accept (and it doesn't clash with A's
other classes). Find cycles of length 2, then 3 (bounded search; the graph per course is small). Choose
a set of disjoint cycles that prefers shorter cycles, then the earliest requests. Recompute when an
offer is added or withdrawn.

### Execution plan (the honest part)

- **If the target section has an open seat** (`ENRLTOT < ENRLCAP` in the ASU data), the student uses
  ASU's own Swap (an atomic drop+add). No risk; no countdown.
- **If every section in the cycle is full**, ASU's Swap fails for everyone: the seat you want is held by
  the person who wants yours. One student has to drop first. The plan names that "anchor", explains the
  risk in one line ("for about 30 seconds your seat is open to anyone"), and runs a countdown:
  T-0 anchor drops → T+5 s next student swaps into the freed seat → … → anchor adds the last freed seat.
  The anchor is the student who opts in to it; if nobody opts in, the swap doesn't run.
- After each step the student taps "done"; a timeout or "it failed" stops the chain and tells everyone
  exactly what to do to get back.

## Phases

1. **Core + simulator (no accounts).** Models, ASU reader with fixtures, agent, exchange, protocol,
   `python -m swap.sim scenarios/chem-3way.yaml` prints a readable transcript of each agent's DMs.
   Tests for clash filtering, cycle finding, priority, the privacy of the payload, and protocol states.
2. **Web demo page.** A static page that replays a simulator transcript as three phones side by side.
   Used for the demo video.
3. **Discord.** One bot, DM commands: `/have`, `/want`, `/status`, buttons for accept / decline / done.
   Needs a bot token from the owner (Keychain, never in the repo).
4. **Video + submit.** Same pipeline as Track 1 (WebKit render, no Chromium). Submit preview → owner's
   yes → send.

## Done when

- `python -m unittest discover -s tests` passes offline.
- The simulator finds the 3-way cycle in the demo scenario, refuses a swap that would clash, and the
  exchange payload carries nothing but the handle, the course and the sections.
- (Phase 3) Three test accounts complete a swap end to end in Discord.

## Not doing

Touching ASU registration, storing ASU credentials, or claiming a seat is guaranteed.
