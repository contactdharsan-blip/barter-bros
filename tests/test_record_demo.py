"""The recorded demo for the static site: deterministic, complete, and in step with the service.

Standard library only; runs `scripts/record_demo.py` in-process.
"""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("record_demo", ROOT / "scripts" / "record_demo.py")
record_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record_demo)


class RecordDemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = record_demo.record()

    def test_deterministic(self):
        self.assertEqual(json.dumps(self.data), json.dumps(record_demo.record()))

    def test_committed_replay_is_current(self):
        committed = json.loads(record_demo.OUT.read_text())
        self.assertEqual(committed, json.loads(json.dumps(self.data)),
                         "web/public/demo-replay.json is stale: run scripts/record_demo.py")

    def test_each_press_was_an_open_button(self):
        steps = self.data["steps"]
        kinds = [s["action"]["kind"] for s in steps]
        self.assertEqual(kinds, ["seed", "market", "message", "press", "press", "press", "press"])
        for before, step in zip(steps, steps[1:]):
            if step["action"]["kind"] == "press":
                open_ids = [a["id"] for line in before["session"]["chat"] for a in line["actions"] if a["open"]]
                self.assertIn(step["action"]["id"], open_ids)

    def test_ends_handed_off_with_profiles_revealed(self):
        last = self.data["steps"][-1]
        t1 = next(t for t in last["exchange"]["trades"] if t["id"] == "T1")
        self.assertEqual(t1["state"], "handed_off")
        kinds = {m["kind"] for s in self.data["steps"] for m in s["wire"]}
        self.assertLessEqual({"offer", "proposal", "vote", "identity", "meetup", "receipt", "done"}, kinds)

    def test_wire_indexes_are_contiguous(self):
        wire = [m["i"] for s in self.data["steps"] for m in s["wire"]]
        self.assertEqual(wire, list(range(len(wire))))


if __name__ == "__main__":
    unittest.main()
