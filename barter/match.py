"""The exchange's matcher: compatibility, edges, loops and chains, and selection.

Edge R <- G ("R would take G's item") needs two things:
1. compatibility, which the exchange checks itself from public listings: G's item satisfies
   one of R's wants (category, attributes, condition);
2. fairness, which only R's agent can answer, because it needs R's private floor and values:
   the exchange asks "would you take this item, giving yours (or nothing)?" and gets yes/no.

Structures (member i gives its item to member i+1):
- a cycle of 2-4 people, the last giving to the first;
- a chain that starts at a giveaway (a listing with no wants) and runs through up to
  MAX_CHAIN handoffs. Every middle member gives their own item onward. The chain ends at a
  member who receives and gives nothing in it: someone with nothing to offer, or whose own
  item no one further down wants. Their item, if any, is not part of the trade.

Selection picks disjoint structures maximizing the number of people who get something, then
preferring shorter structures, then earlier requests, then handle order. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .models import Offer

MIN_CYCLE, MAX_CYCLE = 2, 4
MAX_CHAIN = 4  # handoffs in a chain (so up to 5 people)

# (receiver handle, giver handle, receiver gives its own item?) -> yes/no from the receiver's agent
EdgeAsk = Callable[[str, str, bool], bool]


@dataclass(frozen=True)
class Loop:
    kind: str  # "cycle" or "chain"
    members: tuple[str, ...]  # handles; member i gives to member i+1

    @property
    def served(self) -> int:
        """People who get something."""
        return len(self.members) if self.kind == "cycle" else len(self.members) - 1

    def giver_to(self, handle: str) -> str | None:
        i = self.members.index(handle)
        if self.kind == "cycle":
            return self.members[i - 1]
        return self.members[i - 1] if i > 0 else None

    def receiver_from(self, handle: str) -> str | None:
        i = self.members.index(handle)
        if self.kind == "cycle":
            return self.members[(i + 1) % len(self.members)]
        return self.members[i + 1] if i + 1 < len(self.members) else None

    def receivers(self) -> list[str]:
        return [h for h in self.members if self.giver_to(h) is not None]

    def describe(self) -> str:
        arrows = " -> ".join(self.members)
        return f"{len(self.members)}-way cycle {arrows} -> {self.members[0]}" if self.kind == "cycle" else (
            f"{self.served}-step chain {arrows}")


def compatible(receiver: Offer, giver: Offer) -> bool:
    """Public check: the giver has an item and it satisfies one of the receiver's wants."""
    return receiver.handle != giver.handle and giver.have is not None and receiver.wants_item(giver.have)


class EdgeBook:
    """Edge answers, asked lazily and cached. Only compatible pairs are ever asked about."""

    def __init__(self, offers: list[Offer], ask: EdgeAsk, answers: dict | None = None):
        self.offers = {o.handle: o for o in offers}
        self.ask = ask
        self.answers: dict[tuple[str, str, bool], bool] = {} if answers is None else answers

    def ok(self, receiver: str, giver: str, gives: bool) -> bool:
        if not compatible(self.offers[receiver], self.offers[giver]):
            return False
        if gives and self.offers[receiver].have is None:
            return False
        key = (receiver, giver, gives)
        if key not in self.answers:
            self.answers[key] = self.ask(receiver, giver, gives)
        return self.answers[key]


def find_cycles(order: list[str], edges: EdgeBook) -> list[Loop]:
    """Every cycle of 2-4, each once, rotated to start at its earliest-listed member."""
    rank = {h: i for i, h in enumerate(order)}
    found: dict[tuple[str, ...], Loop] = {}

    def walk(path: list[str]) -> None:
        last = path[-1]
        for nxt in order:
            # `last` gives to `nxt`: nxt receives last's item, and nxt gives its own onward
            if nxt == path[0] and len(path) >= MIN_CYCLE and edges.ok(nxt, last, True):
                start = min(range(len(path)), key=lambda i: rank[path[i]])
                members = tuple(path[start:] + path[:start])
                found.setdefault(members, Loop("cycle", members))
            elif nxt not in path and len(path) < MAX_CYCLE and edges.ok(nxt, last, True):
                walk(path + [nxt])

    for h in order:
        if edges.offers[h].have is not None and edges.offers[h].wants:
            walk([h])
    return list(found.values())


def find_chains(order: list[str], edges: EdgeBook) -> list[Loop]:
    """Every chain from a giveaway, of 1..MAX_CHAIN handoffs."""
    found: list[Loop] = []

    def walk(path: list[str]) -> None:
        last = path[-1]
        for nxt in order:
            if nxt in path:
                continue
            if edges.ok(nxt, last, False):  # nxt receives and stops here
                found.append(Loop("chain", tuple(path + [nxt])))
            if len(path) < MAX_CHAIN and edges.ok(nxt, last, True):  # nxt receives and passes theirs on
                walk(path + [nxt])

    for h in order:
        if edges.offers[h].is_giveaway:
            walk([h])
    return found


def select(loops: list[Loop], seq: dict[str, int]) -> list[Loop]:
    """Disjoint loops: most people served, then shorter, then earliest requests, then handles."""
    loops = sorted(loops, key=lambda l: (len(l.members), sorted(seq[h] for h in l.members), l.members))

    def key(chosen: list[Loop]):
        return (
            -sum(l.served for l in chosen),
            sorted((len(l.members) for l in chosen), reverse=True),
            sorted(seq[h] for l in chosen for h in l.members),
            sorted(l.members for l in chosen),
        )

    best: list[Loop] = []
    best_key = key(best)

    def search(i: int, chosen: list[Loop], used: set[str]) -> None:
        nonlocal best, best_key
        if i == len(loops):
            k = key(chosen)
            if k < best_key:
                best, best_key = list(chosen), k
            return
        loop = loops[i]
        if not used & set(loop.members):
            search(i + 1, chosen + [loop], used | set(loop.members))
        search(i + 1, chosen, used)

    search(0, [], set())
    return best


def find(offers: list[Offer], seq: dict[str, int], ask: EdgeAsk,
         declined: set[Loop] = frozenset(), answers: dict | None = None) -> tuple[list[Loop], list[Loop]]:
    """(chosen loops, every candidate loop) over the open offers. `answers` caches edge answers."""
    order = sorted((o.handle for o in offers), key=seq.__getitem__)
    edges = EdgeBook(offers, ask, answers)
    candidates = [l for l in find_cycles(order, edges) + find_chains(order, edges) if l not in declined]
    return select(candidates, seq), candidates
