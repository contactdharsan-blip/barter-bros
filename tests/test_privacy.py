"""What crosses the exchange, across every scenario: offers carry exactly four fields,
and nothing private (names, other classes, reasons) appears anywhere in the traffic."""

import io
import json
import unittest
from pathlib import Path

from swap.agent import OFFER_KEYS
from swap.sim import run

SCENARIOS = sorted((Path(__file__).resolve().parent.parent / "scenarios").glob("*.json"))

# Envelope keys of the non-offer messages. Any dict that describes a student is an offer.
ENVELOPE_KEYS = {"proposal", "cycle", "handle", "answer", "anchor", "risk", "steps", "at", "action",
                 "from", "to", "step", "status", "reason"}


def dicts(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from dicts(v)
    elif isinstance(value, list):
        for v in value:
            yield from dicts(v)


class Privacy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {path.name: (json.loads(path.read_text()), run(path, out=io.StringIO())) for path in SCENARIOS}

    def test_every_scenario_ran(self):
        self.assertGreaterEqual(len(self.runs), 4)
        for name in ("chem-3way.json", "chem-2way-openseat.json", "clash.json", "decline.json"):
            self.assertIn(name, self.runs)

    def test_offer_payloads_have_exactly_the_four_keys(self):
        count = 0
        for name, (_, result) in self.runs.items():
            for msg in result.exchange.log:
                if msg.kind == "offer":
                    count += 1
                    self.assertEqual(list(msg.payload), list(OFFER_KEYS), name)
                for d in dicts(msg.payload):
                    if "accept" in d or "have" in d:  # a student record
                        self.assertEqual(set(d), set(OFFER_KEYS), f"{name}: {msg.kind}")
        self.assertGreater(count, 0)

    def test_other_messages_use_only_envelope_keys(self):
        for name, (_, result) in self.runs.items():
            for msg in result.exchange.log:
                for d in dicts(msg.payload):
                    if set(d) == set(OFFER_KEYS):
                        continue
                    self.assertLessEqual(set(d), ENVELOPE_KEYS, f"{name}: {msg.kind} {sorted(d)}")

    def test_no_name_other_class_or_reason_crosses_the_exchange(self):
        for name, (scenario, result) in self.runs.items():
            traffic = json.dumps([(m.sender, m.recipient, m.kind, m.payload) for m in result.exchange.log])
            private = []
            for student in scenario["students"]:
                private += student["name"].split()
                private += student.get("reasons", {}).values()
            for extra in scenario.get("extra_sections", []):  # everyone's non-CHM classes
                private += [extra["class_nbr"], extra["course"]]
            for secret in private:
                self.assertNotIn(secret, traffic, f"{name}: {secret!r} leaked")

    def test_only_handles_and_the_exchange_send_or_receive(self):
        for name, (_, result) in self.runs.items():
            handles = {a.handle for a in result.agents}
            for msg in result.exchange.log:
                self.assertLessEqual({msg.sender, msg.recipient}, handles | {"exchange"}, name)


if __name__ == "__main__":
    unittest.main()
