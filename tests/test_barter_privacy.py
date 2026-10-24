"""What crosses the exchange in every Barter Bros scenario: a handle and item listings, yes/no
answers and the public plan. Never a name, contact, floor, private value, availability or reason."""

import io
import json
import re
import unittest
from pathlib import Path

from barter.models import OFFER_KEYS
from barter.sim import run

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = sorted(p for p in (ROOT / "scenarios").glob("*.json") if "people" in json.loads(p.read_text()))

ITEM_KEYS = {"category", "attributes", "condition"}
WANT_KEYS = {"category", "attributes", "min_condition"}
ATTRIBUTE_KEYS = {"connector", "watts", "model", "course", "title", "edition", "kind", "size", "class_nbr"}
# Envelope keys of every non-listing message (edge questions, votes, meetup, receipts, countdown).
ENVELOPE_KEYS = {"trade", "shape", "loop", "from", "item", "you_give", "answer", "handle", "slot", "spot",
                 "reason", "anchor", "risk", "steps", "at", "action", "to", "step", "status"}


def dicts(value, parent=None):
    if isinstance(value, dict):
        yield parent, value
        for k, v in value.items():
            yield from dicts(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from dicts(v, parent)


def scalars(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from scalars(v)
    elif isinstance(value, list):
        for v in value:
            yield from scalars(v)
    else:
        yield value


def leak(secret: str, traffic: str):
    """Whole-token match, so "Mo" doesn't hit "memorial", but "+1 480..." and "...monday." still do."""
    return re.search(rf"(?<![a-z0-9]){re.escape(secret.lower())}(?![a-z0-9])", traffic)


class Privacy(unittest.TestCase):
    def test_the_needle_check_bites(self):
        self.assertTrue(leak("My charger died and I have an exam Monday.",
                             '{"reason": "my charger died and i have an exam monday."}'))
        self.assertTrue(leak("+1 480 555 0117", '{"to": "+1 480 555 0117"}'))
        self.assertTrue(leak("Mo", '{"from": "mo"}'))
        self.assertIsNone(leak("Mo", '{"spot": "memorial union"}'))

    @classmethod
    def setUpClass(cls):
        cls.runs = {p.name: (json.loads(p.read_text()), run(p, out=io.StringIO())) for p in SCENARIOS}

    def test_all_seven_scenarios_ran(self):
        self.assertEqual(sorted(self.runs), sorted(f"{n}.json" for n in (
            "book-charger-case-3way", "moveout-chain", "unfair-refused", "no-show-unwind", "no-common-slot",
            "lightning-vs-usbc", "mixed-with-section")))

    def test_offers_carry_only_the_allowed_keys(self):
        count = 0
        for name, (_, result) in self.runs.items():
            for msg in result.exchange.log:
                if msg.kind == "offer":
                    count += 1
                    self.assertEqual(list(msg.payload), list(OFFER_KEYS), name)
                for parent, d in dicts(msg.payload):
                    if "wants" in d:  # a listing, wherever it appears (offers, proposals)
                        self.assertEqual(set(d), set(OFFER_KEYS), f"{name}: {msg.kind}")
                    if parent in ("have", "item"):
                        self.assertEqual(set(d), ITEM_KEYS, f"{name}: {msg.kind}")
                    if parent == "wants":
                        self.assertEqual(set(d), WANT_KEYS, f"{name}: {msg.kind}")
                    if parent == "attributes":
                        self.assertLessEqual(set(d), ATTRIBUTE_KEYS, f"{name}: {msg.kind}")
        self.assertGreater(count, 20)

    def test_other_messages_use_only_envelope_keys(self):
        for name, (_, result) in self.runs.items():
            for msg in result.exchange.log:
                for parent, d in dicts(msg.payload):
                    if parent in ("have", "item", "wants", "attributes") or "wants" in d:
                        continue
                    self.assertLessEqual(set(d), ENVELOPE_KEYS, f"{name}: {msg.kind} {sorted(d)}")

    def test_no_name_contact_or_reason_crosses(self):
        for name, (scenario, result) in self.runs.items():
            traffic = json.dumps([(m.sender, m.recipient, m.kind, m.payload) for m in result.exchange.log]).lower()
            for person in scenario["people"]:
                secrets = person["name"].split() + [person.get("contact", ""), person.get("reasons", "")]
                secrets += person.get("free", [])
                for secret in filter(None, secrets):
                    self.assertIsNone(leak(secret, traffic), f"{name}: {secret!r} leaked")
            for extra in scenario.get("extra_sections", []):  # everyone's other classes
                self.assertNotIn(extra["class_nbr"], traffic, name)

    def test_floors_and_private_values_never_cross(self):
        checked = 0
        for name, (scenario, result) in self.runs.items():
            private = set()
            for agent in result.agents:
                if agent.listing.floor is not None:
                    private.add(agent.listing.floor)
                private |= set(agent.values.values())
            checked += len(private)
            numbers = {v for m in result.exchange.log for v in scalars(m.payload)
                       if isinstance(v, (int, float)) and not isinstance(v, bool)}
            self.assertEqual(numbers & private, set(), f"{name}: a floor or private value crossed")
            text = json.dumps([m.payload for m in result.exchange.log])
            self.assertNotIn("$", text, name)
            for word in ("floor", "value", "worth", "price"):
                self.assertNotIn(word, text.lower(), f"{name}: {word!r} in traffic")
        self.assertGreaterEqual(checked, 5)  # the scenarios do set floors and values

    def test_only_handles_and_the_exchange_send_or_receive(self):
        for name, (_, result) in self.runs.items():
            handles = {a.handle for a in result.agents}
            for msg in result.exchange.log:
                self.assertLessEqual({msg.sender, msg.recipient}, handles | {"exchange"}, name)

    def test_answers_are_bare_yes_or_no(self):
        for name, (_, result) in self.runs.items():
            for msg in result.exchange.log:
                if msg.kind in ("edge", "free", "spot", "vote", "plan-vote", "anchor"):
                    self.assertIn(msg.payload["answer"], ("yes", "no"), f"{name}: {msg.kind}")


if __name__ == "__main__":
    unittest.main()
