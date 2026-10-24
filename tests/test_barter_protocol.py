"""Meetup negotiation, trade states, unwinding and re-listing."""

import unittest

from barter.match import Loop
from barter.meetup import SPOTS, Block, Slot, Window, negotiate
from barter.models import Item, Offer, Want
from barter.protocol import Exchange, ProtocolError, State, Trade
from swap.models import parse_clock

SAT = Window(("SA",), parse_clock("10:00 AM"), parse_clock("12:00 PM"))


class Meetup(unittest.TestCase):
    def test_earliest_common_slot_and_first_agreed_spot(self):
        free = {"a": [Block.parse("SA 10:00 AM-12:00 PM")], "b": [Block.parse("SA 10:45 AM-12:00 PM")]}
        spots = {"a": {"Hayden Library", "Noble Library"}, "b": {"Noble Library"}}
        asked = []

        def is_free(h, slot):
            asked.append((h, slot.label()))
            return any(b.covers(slot) for b in free[h])

        meetup, why = negotiate(["a", "b"], SAT, is_free, lambda h, s: s in spots[h])
        self.assertEqual(why, "")
        self.assertEqual(meetup.slot.label(), "Sat 11:00 AM-11:30 AM")  # 10:30 doesn't fit b's 10:45 start
        self.assertEqual(meetup.spot, "Noble Library")
        self.assertIn(("a", "Sat 10:00 AM-10:30 AM"), asked)

    def test_no_common_slot_gives_a_reason(self):
        free = {"a": [Block.parse("SA 10:00 AM-11:00 AM")], "b": [Block.parse("SA 11:00 AM-12:00 PM")]}
        meetup, why = negotiate(["a", "b"], SAT, lambda h, s: any(b.covers(s) for b in free[h]), lambda h, s: True)
        self.assertIsNone(meetup)
        self.assertIn("no 30-minute slot", why)

    def test_no_common_spot_gives_a_reason(self):
        spots = {"a": {"Hayden Library"}, "b": {"Student Pavilion"}}
        meetup, why = negotiate(["a", "b"], SAT, lambda h, s: True, lambda h, s: s in spots[h])
        self.assertIsNone(meetup)
        self.assertIn("no public spot", why)

    def test_slots_are_thirty_minutes_in_time_order(self):
        labels = [s.label() for s in SAT.slots()]
        self.assertEqual(labels[0], "Sat 10:00 AM-10:30 AM")
        self.assertEqual(labels[-1], "Sat 11:30 AM-12:00 PM")
        self.assertEqual(len(labels), 4)
        self.assertFalse(Block.parse("SA 10:00 AM-10:20 AM").covers(Slot(5, parse_clock("10:00 AM"))))
        self.assertEqual(SPOTS[0], "Memorial Union")


def three_way():
    offers = {
        "a": Offer("a", Item("textbook", {"course": "BIO 181"}), (Want("charger"),)),
        "b": Offer("b", Item("phone_case", {"model": "iPhone 15"}), (Want("textbook"),)),
        "c": Offer("c", Item("charger", {"connector": "usb-c"}), (Want("phone_case"),)),
    }
    return Trade("T1", Loop("cycle", ("a", "b", "c")), offers)


def agreed():
    t = three_way()
    for h in "abc":
        t.vote(h, True)
    t.propose_plan("Sat 11:00 at Noble")
    for h in "abc":
        t.confirm_plan(h, True)
    return t


class States(unittest.TestCase):
    def test_happy_path(self):
        t = three_way()
        self.assertEqual(t.state, State.PROPOSED)
        for h in "abc":
            t.vote(h, True)
        self.assertEqual(t.state, State.ACCEPTED_BY_ALL)
        t.propose_plan("plan")
        for h in "ab":
            t.confirm_plan(h, True)
        self.assertEqual(t.state, State.ACCEPTED_BY_ALL)
        t.confirm_plan("c", True)
        self.assertEqual(t.state, State.MEETUP_AGREED)
        for h in "abc":
            t.receipt(h, True)
        self.assertEqual(t.state, State.HANDED_OFF)
        self.assertEqual(t.gets("a").category, "charger")
        self.assertEqual(t.gives("a").category, "textbook")

    def test_any_no_or_no_show_or_missing_item_unwinds(self):
        t = three_way()
        t.vote("a", True)
        t.vote("b", False)
        self.assertEqual(t.state, State.UNWOUND)

        t = three_way()
        for h in "abc":
            t.vote(h, True)
        t.propose_plan("plan")
        t.confirm_plan("a", False)
        self.assertEqual((t.state, t.reason), (State.UNWOUND, "someone said no to the meetup plan"))

        t = agreed()
        t.no_show("b")
        self.assertEqual((t.state, t.reason), (State.UNWOUND, "b didn't show up"))

        t = agreed()
        t.receipt("a", True)
        t.receipt("c", False)
        self.assertEqual(t.state, State.UNWOUND)

    def test_out_of_order_steps_are_refused(self):
        t = three_way()
        with self.assertRaises(ProtocolError):
            t.receipt("a", True)
        with self.assertRaises(ProtocolError):
            t.confirm_plan("a", True)
        t = agreed()
        with self.assertRaises(ProtocolError):
            t.vote("a", True)

    def test_chain_giver_receives_nothing_so_gives_no_receipt(self):
        offers = {"g": Offer("g", Item("furniture", {"kind": "desk"}), ()),
                  "r": Offer("r", None, (Want("furniture"),))}
        t = Trade("T1", Loop("chain", ("g", "r")), offers)
        for h in "gr":
            t.vote(h, True)
        t.propose_plan("plan")
        for h in "gr":
            t.confirm_plan(h, True)
        with self.assertRaises(ProtocolError):
            t.receipt("g", True)
        t.receipt("r", True)
        self.assertEqual(t.state, State.HANDED_OFF)


class Relisting(unittest.TestCase):
    def setUp(self):
        self.ex = Exchange()
        self.ex.add({"handle": "a", "have": {"category": "charger", "attributes": {"connector": "lightning"}, "condition": "good"},
                     "wants": [{"category": "furniture", "attributes": {"kind": "desk lamp"}, "min_condition": None}]})
        self.ex.add({"handle": "b", "have": {"category": "furniture", "attributes": {"kind": "desk lamp"}, "condition": "good"},
                     "wants": [{"category": "charger", "attributes": {"connector": "lightning"}, "min_condition": None}]})
        self.yes = lambda r, g, gives: True

    def test_an_unwound_trade_relists_everyone_and_is_not_proposed_again(self):
        [t] = self.ex.match(self.yes)
        self.assertEqual(self.ex.open_offers(), [])  # locked while live
        t.vote("a", True)
        t.vote("b", False)
        self.ex.close(t)
        self.assertEqual([o.handle for o in self.ex.open_offers()], ["a", "b"])
        self.assertEqual(self.ex.match(self.yes), [])

    def test_a_handed_off_trade_takes_its_listings_down(self):
        [t] = self.ex.match(self.yes)
        for h in "ab":
            t.vote(h, True)
        t.propose_plan("plan")
        for h in "ab":
            t.confirm_plan(h, True)
        for h in "ab":
            t.receipt(h, True)
        self.ex.close(t)
        self.assertEqual(self.ex.book, {})

    def test_a_live_trade_cannot_be_closed(self):
        [t] = self.ex.match(self.yes)
        with self.assertRaises(ProtocolError):
            self.ex.close(t)

    def test_edge_answers_are_cached(self):
        calls = []

        def ask(r, g, gives):
            calls.append((r, g))
            return False

        self.ex.match(ask)
        self.ex.match(ask)
        self.assertEqual(len(calls), 2)  # a<-b and b<-a, once each

    def test_offer_payload_must_have_exactly_the_three_keys(self):
        with self.assertRaises(ValueError):
            self.ex.add({"handle": "x", "have": None, "wants": [], "floor": 30})


if __name__ == "__main__":
    unittest.main()
