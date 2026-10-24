import unittest

from swap.agent import Offer
from swap.exchange import Exchange, choose, find_cycles


def o(handle, have, *accept):
    return Offer(handle, "CHM 113", have, tuple(accept))


def handles(cycle):
    return [x.handle for x in cycle]


class Cycles(unittest.TestCase):
    def test_two_cycle(self):
        cycles = find_cycles([o("a", "1", "2"), o("b", "2", "1")])
        self.assertEqual([handles(c) for c in cycles], [["a", "b"]])

    def test_three_cycle_found_once_starting_at_first_offer(self):
        offers = [o("a", "1", "2"), o("b", "2", "3"), o("c", "3", "1")]
        cycles = find_cycles(offers)
        self.assertEqual([handles(c) for c in cycles], [["a", "b", "c"]])

    def test_no_cycle_longer_than_three(self):
        offers = [o("a", "1", "2"), o("b", "2", "3"), o("c", "3", "4"), o("d", "4", "1")]
        self.assertEqual(find_cycles(offers), [])

    def test_courses_do_not_mix(self):
        offers = [o("a", "1", "2"), Offer("b", "BIO 181", "2", ("1",))]
        self.assertEqual(find_cycles(offers), [])

    def test_shorter_cycle_wins_a_shared_member(self):
        # a<->b is a 2-cycle; a->c->d->a is a 3-cycle sharing a.
        offers = [o("a", "1", "2", "3"), o("c", "3", "4"), o("d", "4", "1"), o("b", "2", "1")]
        seq = {(x.handle, x.course): i for i, x in enumerate(offers)}
        chosen = choose(find_cycles(offers), seq)
        self.assertEqual([handles(c) for c in chosen], [["a", "b"]])

    def test_first_come_wins_between_equal_lengths(self):
        # b and c both hold seat 2 and want seat 1; a accepts either. b listed first.
        offers = [o("a", "1", "2"), o("b", "2", "1"), o("c", "2", "1")]
        seq = {(x.handle, x.course): i for i, x in enumerate(offers)}
        chosen = choose(find_cycles(offers), seq)
        self.assertEqual([handles(c) for c in chosen], [["a", "b"]])

    def test_disjoint_cycles_both_chosen(self):
        offers = [o("a", "1", "2"), o("b", "2", "1"), o("c", "3", "4"), o("d", "4", "3")]
        seq = {(x.handle, x.course): i for i, x in enumerate(offers)}
        self.assertEqual(len(choose(find_cycles(offers), seq)), 2)


class Book(unittest.TestCase):
    def test_proposal_appears_when_cycle_closes(self):
        ex = Exchange()
        self.assertEqual(ex.add(o("a", "1", "2").payload()), [])
        new = ex.add(o("b", "2", "1").payload())
        self.assertEqual([handles(p.cycle) for p in new], [["a", "b"]])

    def test_recompute_on_withdrawal_frees_the_other_cycle(self):
        ex = Exchange()
        ex.add(o("a", "1", "2").payload())
        ex.add(o("x", "9", "8").payload())
        (p,) = ex.add(o("b", "2", "1").payload())
        # c also holds seat 2 and wants seat 1, but a is locked into P1.
        self.assertEqual(ex.add(o("c", "2", "1").payload()), [])
        # b withdraws before voting: P1 is cancelled and the recompute finds a<->c.
        new = ex.withdraw("b", "CHM 113")
        self.assertEqual(p.state.value, "CANCELLED")
        self.assertEqual([handles(q.cycle) for q in new], [["a", "c"]])

    def test_withdraw_removes_the_offer_from_matching(self):
        ex = Exchange()
        ex.add(o("a", "1", "2").payload())
        ex.withdraw("a", "CHM 113")
        self.assertEqual(ex.add(o("b", "2", "1").payload()), [])

    def test_declined_cycle_is_not_proposed_again(self):
        ex = Exchange()
        ex.add(o("a", "1", "2").payload())
        (p,) = ex.add(o("b", "2", "1").payload())
        p.vote("a", False)
        self.assertEqual(ex.close(p), [])
        self.assertEqual(ex.recompute(), [])


if __name__ == "__main__":
    unittest.main()
