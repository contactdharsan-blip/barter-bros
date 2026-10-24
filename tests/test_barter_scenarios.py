"""Every Barter Bros scenario runs (as a command, exit 0) and ends the way its description says."""

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from barter.protocol import State
from barter.sim import run

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"
BARTER = sorted(p for p in SCENARIOS.glob("*.json") if "people" in json.loads(p.read_text()))

# scenario -> [(shape, people, final state)] in the order trades were proposed
EXPECTED = {
    "book-charger-case-3way": [("cycle", 3, "handed_off")],
    "moveout-chain": [("chain", 4, "handed_off")],
    "unfair-refused": [("cycle", 2, "handed_off")],
    "no-show-unwind": [("cycle", 2, "unwound"), ("cycle", 2, "handed_off")],
    "no-common-slot": [("cycle", 2, "unwound")],
    "lightning-vs-usbc": [("cycle", 2, "handed_off")],
    "mixed-with-section": [("cycle", 3, "handed_off"), ("cycle", 2, "handed_off")],
}


def names(result, trade):
    by_handle = {a.handle: a.name.split()[0] for a in result.agents}
    return [by_handle[h] for h in trade.handles]


class Scenarios(unittest.TestCase):
    def test_every_scenario_runs_from_the_command_line(self):
        self.assertEqual(len(BARTER), 7)
        for path in BARTER:
            done = subprocess.run([sys.executable, "-m", "barter.sim", str(path.relative_to(ROOT))],
                                  cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, f"{path.name}: {done.stderr}")
            self.assertIn("=== End of run ===", done.stdout)

    def test_expected_endings(self):
        for path in BARTER:
            result = run(path, out=io.StringIO())
            got = [(t.loop.kind, len(t.handles), t.state.value) for t in result.trades]
            self.assertEqual(got, EXPECTED[path.stem], path.name)

    def test_every_request_was_read_by_the_rules_parser_offline(self):
        for path in BARTER:
            result = run(path, out=io.StringIO())
            for agent in result.agents:
                self.assertIn(agent.listing.parser, ("rules", "sections"), f"{path.name}: {agent.name}")

    def test_the_moment(self):
        out = io.StringIO()
        result = run(SCENARIOS / "book-charger-case-3way.json", out=out)
        [trade] = result.trades
        by_name = {a.name.split()[0]: a.handle for a in result.agents}
        loop = trade.loop
        self.assertEqual(loop.receiver_from(by_name["Jordan"]), by_name["Sam"])  # book -> Sam
        self.assertEqual(loop.receiver_from(by_name["Sam"]), by_name["Priya"])  # phone case -> Priya
        self.assertEqual(loop.receiver_from(by_name["Priya"]), by_name["Jordan"])  # charger -> Jordan
        text = out.getvalue()
        for heading in ("confirmation (1), the loop", "confirmation (2), the meetup plan", "confirmation (3), the handoff"):
            self.assertIn(heading, text)
        self.assertIn("with the rules parser", text)
        self.assertIn("Noble Library", text)
        self.assertIn(f"{by_name['Leo']} also wanted", text)  # contention

    def test_unfair_loop_is_blocked_by_a_private_floor(self):
        out = io.StringIO()
        result = run(SCENARIOS / "unfair-refused.json", out=out)
        self.assertEqual(names(result, result.trades[0]), ["Cal", "Dana"])
        alex = next(a for a in result.agents if a.name.startswith("Alex"))
        self.assertIn("below your $61 floor", out.getvalue())
        self.assertIn(alex.handle, [o.handle for o in result.exchange.open_offers()])

    def test_chain_runs_from_the_giveaway(self):
        result = run(SCENARIOS / "moveout-chain.json", out=io.StringIO())
        self.assertEqual(names(result, result.trades[0]), ["Maya", "Ravi", "Ana", "Tom"])

    def test_no_show_relists_and_a_new_partner_is_found(self):
        result = run(SCENARIOS / "no-show-unwind.json", out=io.StringIO())
        first, second = result.trades
        self.assertIn("didn't show up", first.reason)
        self.assertEqual(names(result, second), ["Kai", "Mo"])
        self.assertEqual(second.state, State.HANDED_OFF)

    def test_no_common_slot_reason(self):
        result = run(SCENARIOS / "no-common-slot.json", out=io.StringIO())
        self.assertIn("no 30-minute slot", result.trades[0].reason)
        self.assertEqual(len(result.exchange.open_offers()), 2)

    def test_lightning_never_matches_usbc(self):
        out = io.StringIO()
        result = run(SCENARIOS / "lightning-vs-usbc.json", out=out)
        self.assertEqual(names(result, result.trades[0]), ["Quinn", "Ray"])
        pia = next(a for a in result.agents if a.name.startswith("Pia"))
        self.assertFalse(any(m.recipient == next(a.handle for a in result.agents if a.name.startswith("Quinn"))
                             and m.kind == "edge?" and m.payload["from"] == pia.handle for m in result.exchange.log))
        self.assertIn("connector is lightning, wanted usb-c", out.getvalue())

    def test_section_loop_runs_on_the_swap_countdown(self):
        out = io.StringIO()
        result = run(SCENARIOS / "mixed-with-section.json", out=out)
        section, things = result.trades
        self.assertEqual(sorted(names(result, section)), ["Ava", "Ben", "Cam"])
        self.assertIsNotNone(section.plan.anchor)  # every section full: Ben opted in
        self.assertEqual(section.kind, "countdown")
        self.assertEqual(things.kind, "meetup")
        self.assertIn("starts before 10:00 AM", out.getvalue())  # Ava's agent ruled one out privately


if __name__ == "__main__":
    unittest.main()
