import unittest

from swap.agent import Offer
from swap.protocol import Proposal, ProtocolError, State, make_plan, needs_anchor

# a gets b's seat 2, b gets c's seat 3, c gets a's seat 1.
CYCLE = (Offer("a", "CHM 113", "1", ("2",)), Offer("b", "CHM 113", "2", ("3",)), Offer("c", "CHM 113", "3", ("1",)))
ALL_FULL = {"1": False, "2": False, "3": False}


def moves(plan):
    return [(s.at, s.handle, s.action, s.from_nbr, s.to_nbr) for s in plan.steps]


def accepted(cycle=CYCLE):
    p = Proposal("P1", cycle)
    for h in p.handles:
        p.vote(h, True)
    return p


def running(anchor="b"):
    p = accepted()
    p.make_plan(ALL_FULL, anchor)
    for h in p.handles:
        p.confirm_plan(h, True)
    return p


class Lifecycle(unittest.TestCase):
    def test_happy_path_through_every_state(self):
        p = Proposal("P1", CYCLE)
        self.assertIs(p.state, State.PROPOSED)
        p.vote("a", True)
        p.vote("b", True)
        self.assertIs(p.state, State.PROPOSED)
        p.vote("c", True)
        self.assertIs(p.state, State.ACCEPTED_BY_ALL)
        p.make_plan(ALL_FULL, "b")
        self.assertIs(p.state, State.PLANNED)
        for h in p.handles:
            p.confirm_plan(h, True)
        self.assertIs(p.state, State.RUNNING)
        for step in p.plan.steps:
            p.report(step.handle, "done")
        self.assertIs(p.state, State.DONE)

    def test_one_no_cancels_the_cycle(self):
        p = Proposal("P1", CYCLE)
        p.vote("a", True)
        p.vote("b", False)
        self.assertIs(p.state, State.CANCELLED)
        with self.assertRaises(ProtocolError):
            p.vote("c", True)

    def test_no_to_the_plan_cancels(self):
        p = accepted()
        p.make_plan(ALL_FULL, "b")
        p.confirm_plan("a", True)
        p.confirm_plan("c", False)
        self.assertIs(p.state, State.CANCELLED)

    def test_cannot_plan_before_everyone_accepts(self):
        p = Proposal("P1", CYCLE)
        p.vote("a", True)
        with self.assertRaises(ProtocolError):
            p.make_plan(ALL_FULL, "a")

    def test_report_out_of_turn_is_refused(self):
        p = running()
        with self.assertRaises(ProtocolError):
            p.report("a", "done")  # b drops first


class Anchor(unittest.TestCase):
    def test_all_full_needs_an_anchor(self):
        self.assertTrue(needs_anchor(CYCLE, ALL_FULL))
        self.assertFalse(needs_anchor(CYCLE, {**ALL_FULL, "3": True}))

    def test_no_opt_in_means_cancelled(self):
        p = accepted()
        self.assertIsNone(p.make_plan(ALL_FULL, anchor=None))
        self.assertIs(p.state, State.CANCELLED)
        self.assertIn("nobody opted in", p.reason)

    def test_anchor_plan_walks_backwards_round_the_cycle(self):
        plan = make_plan(CYCLE, ALL_FULL, anchor="b")
        self.assertEqual(plan.anchor, "b")
        self.assertEqual(
            moves(plan),
            [
                (0, "b", "drop", "2", None),  # b frees seat 2...
                (5, "a", "swap", "1", "2"),  # ...which a wants; a frees seat 1...
                (10, "c", "swap", "3", "1"),  # ...which c wants; c frees seat 3...
                (15, "b", "add", None, "3"),  # ...which b adds last.
            ],
        )
        self.assertIn("15 seconds", plan.risk)

    def test_open_seat_means_atomic_swaps_and_no_anchor(self):
        # a's target, seat 2, has room: a swaps first, freeing 1 for c, then c frees 3 for b.
        plan = make_plan(CYCLE, {**ALL_FULL, "2": True})
        self.assertIsNone(plan.anchor)
        self.assertEqual(moves(plan), [(0, "a", "swap", "1", "2"), (5, "c", "swap", "3", "1"), (10, "b", "swap", "2", "3")])
        self.assertTrue(plan.risk.startswith("None"))

    def test_two_way_with_open_seat(self):
        cycle = (Offer("d", "CHM 113", "11989", ("17344",)), Offer("e", "CHM 113", "17344", ("11989",)))
        plan = make_plan(cycle, {"11989": False, "17344": True})
        self.assertEqual(moves(plan), [(0, "d", "swap", "11989", "17344"), (5, "e", "swap", "17344", "11989")])


class Recovery(unittest.TestCase):
    def test_failed_swap_stops_the_chain_and_tells_everyone(self):
        p = running(anchor="b")
        p.report("b", "done")  # b dropped seat 2
        p.report("a", "failed")  # a's swap into 2 failed: a stranger took it
        self.assertIs(p.state, State.FAILED)
        self.assertIsNone(p.next_step)
        with self.assertRaises(ProtocolError):
            p.report("c", "done")
        advice = p.recovery(ALL_FULL)
        self.assertIn("still hold #1", advice["a"])
        self.assertIn("no seat", advice["b"])
        self.assertIn("waitlist for #3", advice["b"])
        self.assertIn("Do nothing. You still hold #3", advice["c"])

    def test_anchor_told_to_re_add_if_their_old_seat_is_open(self):
        p = running(anchor="b")
        p.report("b", "done")
        p.report("a", "timed out")
        advice = p.recovery({**ALL_FULL, "2": True})
        self.assertIn("add it back", advice["b"])
        self.assertIn("Check your schedule", advice["a"])

    def test_student_who_already_moved_keeps_the_new_seat(self):
        p = running(anchor="b")
        p.report("b", "done")
        p.report("a", "done")
        p.report("c", "failed")
        advice = p.recovery(ALL_FULL)
        self.assertIn("You moved to #2", advice["a"])
        self.assertIn("still hold #3", advice["c"])

    def test_failed_final_add(self):
        p = running(anchor="b")
        for h in ("b", "a", "c"):
            p.report(h, "done")
        p.report("b", "failed")
        advice = p.recovery({**ALL_FULL, "3": True})
        self.assertIn("#3 is still open", advice["b"])


if __name__ == "__main__":
    unittest.main()
