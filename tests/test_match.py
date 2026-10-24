"""Compatibility, fairness edges, cycle and chain search, and selection order."""

import random
import unittest

from barter.agent import StudentAgent
from barter.match import EdgeBook, Loop, compatible, find, find_chains, find_cycles, select
from barter.models import Item, Listing, Offer, Want

USBC = Item("charger", {"connector": "usb-c", "watts": 20}, "good")
LIGHTNING = Item("charger", {"connector": "lightning"}, "good")
BOOK = Item("textbook", {"course": "BIO 181", "edition": "3"}, "good")
CASE = Item("phone_case", {"model": "iPhone 15"}, "new")
DESK = Item("furniture", {"kind": "desk"}, "good")
CALC = Item("calculator", {"model": "TI-84"}, "good")


def offer(handle, have, *wants):
    return Offer(handle, have, tuple(wants))


def always(receiver, giver, gives):
    return True


class Compatibility(unittest.TestCase):
    def test_attributes_condition_and_watts(self):
        self.assertTrue(Want("charger", {"connector": "usb-c"}).matches(USBC))
        self.assertFalse(Want("charger", {"connector": "usb-c"}).matches(LIGHTNING))
        self.assertIn("lightning", Want("charger", {"connector": "usb-c"}).why_not(LIGHTNING))
        self.assertTrue(Want("charger", {"watts": 18}).matches(USBC))
        self.assertFalse(Want("charger", {"watts": 30}).matches(USBC))
        self.assertTrue(Want("textbook", {"course": "BIO 181"}).matches(BOOK))
        self.assertFalse(Want("textbook", {"course": "BIO 181", "edition": "4"}).matches(BOOK))
        self.assertFalse(Want("phone_case", {"model": "iPhone 14"}).matches(CASE))
        self.assertTrue(Want("phone_case", min_condition="good").matches(CASE))
        self.assertFalse(Want("furniture", min_condition="new").matches(DESK))
        self.assertFalse(Want("charger").matches(BOOK))

    def test_compatible_needs_an_item_and_a_matching_want(self):
        a = offer("a", BOOK, Want("charger", {"connector": "usb-c"}))
        self.assertTrue(compatible(a, offer("b", USBC, Want("textbook"))))
        self.assertFalse(compatible(a, offer("c", LIGHTNING, Want("textbook"))))
        self.assertFalse(compatible(a, offer("d", None, Want("textbook"))))
        self.assertFalse(compatible(a, a))

    def test_est_value_is_a_table_lookup(self):
        self.assertEqual(Item("textbook", {}, "new").est_value, 90)
        self.assertEqual(Item("charger", {}, "fair").est_value, 8)


class Fairness(unittest.TestCase):
    def agent(self, floor, values=None):
        listing = Listing(BOOK, [Want("charger", {"connector": "usb-c"})], floor=floor)
        return StudentAgent("Private Person", listing, values=values or {}, rng=random.Random(1))

    def test_edge_needs_value_at_or_above_floor(self):
        self.assertFalse(self.agent(30).accepts(USBC, gives=True))  # $15 table value < $30
        self.assertTrue(self.agent(30, {"charger": 35}).accepts(USBC, gives=True))
        self.assertTrue(self.agent(15).accepts(USBC, gives=True))  # equal is enough
        self.assertTrue(self.agent(None).accepts(USBC, gives=True))  # no floor set

    def test_receiving_without_giving_skips_the_floor(self):
        self.assertTrue(self.agent(500).accepts(USBC, gives=False))

    def test_want_must_match_regardless_of_value(self):
        self.assertFalse(self.agent(0, {"charger": 999}).accepts(LIGHTNING, gives=True))

    def test_the_reason_stays_in_the_agent(self):
        a = self.agent(30)
        self.assertIs(a.accepts(USBC, gives=True), False)  # a bare bool is all the caller gets
        self.assertIn("$30 floor", a.decisions[-1][1].why)


class Search(unittest.TestCase):
    def setUp(self):
        # you: book -> wants charger; sam: case -> wants book; priya: charger -> wants case
        self.offers = [
            offer("you", BOOK, Want("charger", {"connector": "usb-c"})),
            offer("sam", CASE, Want("textbook", {"course": "BIO 181"})),
            offer("priya", USBC, Want("phone_case", {"model": "iPhone 15"})),
        ]
        self.order = ["you", "sam", "priya"]

    def test_three_cycle_found_once_in_giving_order(self):
        cycles = find_cycles(self.order, EdgeBook(self.offers, always))
        self.assertEqual(cycles, [Loop("cycle", ("you", "sam", "priya"))])
        loop = cycles[0]
        self.assertEqual(loop.receiver_from("you"), "sam")  # book -> Sam
        self.assertEqual(loop.receiver_from("sam"), "priya")  # case -> Priya
        self.assertEqual(loop.receiver_from("priya"), "you")  # charger -> you

    def test_a_no_on_one_edge_kills_the_cycle(self):
        def ask(receiver, giver, gives):
            return receiver != "you"

        self.assertEqual(find_cycles(self.order, EdgeBook(self.offers, ask)), [])

    def test_only_compatible_pairs_are_asked(self):
        asked = []

        def ask(receiver, giver, gives):
            asked.append((receiver, giver))
            return True

        find_cycles(self.order, EdgeBook(self.offers, ask))
        self.assertEqual(sorted(asked), [("priya", "sam"), ("sam", "you"), ("you", "priya")])

    def test_four_cycle_but_not_five(self):
        items = [Item("other", {"kind": f"thing{i}"}) for i in range(5)]
        for n, expect in ((4, 1), (5, 0)):
            offers = [offer(f"p{i}", items[i], Want("other", {"kind": f"thing{(i - 1) % n}"})) for i in range(n)]
            self.assertEqual(len(find_cycles([o.handle for o in offers], EdgeBook(offers, always))), expect, n)

    def test_chain_from_a_giveaway_ends_where_nobody_takes_it_further(self):
        offers = [
            offer("maya", DESK),  # giveaway
            offer("ravi", CALC, Want("furniture", {"kind": "desk"})),
            offer("ana", Item("clothing", {"kind": "hoodie", "size": "M"}), Want("calculator")),
            offer("tom", None, Want("clothing", {"size": "M"})),
        ]
        chains = find_chains([o.handle for o in offers], EdgeBook(offers, always))
        self.assertIn(Loop("chain", ("maya", "ravi", "ana", "tom")), chains)
        self.assertIn(Loop("chain", ("maya", "ravi")), chains)  # shorter alternatives exist too
        chosen, _ = find(offers, {o.handle: i for i, o in enumerate(offers)}, always)
        self.assertEqual(chosen, [Loop("chain", ("maya", "ravi", "ana", "tom"))])  # most people served
        self.assertEqual(chosen[0].served, 3)
        self.assertEqual(chosen[0].receivers(), ["ravi", "ana", "tom"])

    def test_a_middle_member_who_would_not_give_cuts_the_chain(self):
        offers = [offer("maya", DESK), offer("ravi", CALC, Want("furniture")), offer("ana", None, Want("calculator"))]

        def ask(receiver, giver, gives):
            return not (receiver == "ravi" and gives)  # Ravi takes the desk only if he keeps his TI-84

        chosen, _ = find(offers, {"maya": 0, "ravi": 1, "ana": 2}, ask)
        self.assertEqual(chosen, [Loop("chain", ("maya", "ravi"))])


class Selection(unittest.TestCase):
    seq = {h: i for i, h in enumerate("abcdefgh")}

    def test_most_people_served_first(self):
        two = Loop("cycle", ("a", "b"))
        three = Loop("cycle", ("b", "c", "d"))
        self.assertEqual(select([two, three], self.seq), [three])

    def test_shorter_loops_at_equal_people(self):
        four = Loop("cycle", ("a", "b", "c", "d"))
        pair1, pair2 = Loop("cycle", ("a", "b")), Loop("cycle", ("c", "d"))
        self.assertEqual(select([four, pair1, pair2], self.seq), [pair1, pair2])

    def test_earlier_requests_break_ties(self):
        early, late = Loop("cycle", ("a", "c")), Loop("cycle", ("b", "c"))
        self.assertEqual(select([late, early], self.seq), [early])

    def test_disjoint_and_deterministic(self):
        loops = [Loop("cycle", ("a", "b")), Loop("cycle", ("b", "c")), Loop("cycle", ("c", "d")),
                 Loop("chain", ("e", "a", "f"))]
        first = select(loops, self.seq)
        for _ in range(5):
            self.assertEqual(select(list(reversed(loops)), self.seq), first)
        members = [h for l in first for h in l.members]
        self.assertEqual(len(members), len(set(members)))


if __name__ == "__main__":
    unittest.main()
