"""The scenario files do what their descriptions say."""

import io
import json
import unittest
from pathlib import Path

from swap.exchange import find_cycles
from swap.sim import Sim, run

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios"


class Scenarios(unittest.TestCase):
    def test_chem_3way_has_exactly_one_three_cycle_and_no_two_cycle(self):
        scenario = json.loads((SCENARIOS / "chem-3way.json").read_text())
        sim = Sim(scenario, out=io.StringIO())
        offers = [a.offer(w, sim.catalog) for a in sim.agents for w in a.wants]
        self.assertEqual([len(c) for c in find_cycles([x for x in offers if x])], [3])

    def test_expected_endings(self):
        expected = {
            "chem-3way.json": ["DONE"],
            "chem-2way-openseat.json": ["DONE"],
            "clash.json": [],
            "decline.json": ["CANCELLED"],
            "chem-3way-stranger.json": ["FAILED"],
        }
        for path in sorted(SCENARIOS.glob("*.json")):
            if "students" not in json.loads(path.read_text()):
                continue  # a Barter Bros scenario; see tests/test_barter_scenarios.py
            result = run(path, out=io.StringIO())
            self.assertEqual([p.state.value for p in result.proposals], expected[path.name], path.name)

    def test_chem_3way_needs_an_anchor_and_the_clash_student_posts_nothing(self):
        out = io.StringIO()
        result = run(SCENARIOS / "chem-3way.json", out=out)
        self.assertIsNotNone(result.proposals[0].plan.anchor)
        out = io.StringIO()
        result = run(SCENARIOS / "clash.json", out=out)
        self.assertEqual([m.kind for m in result.exchange.log], ["offer"])  # only Gus posts
        self.assertIn("clashes with your BIO 181", out.getvalue())


if __name__ == "__main__":
    unittest.main()
