"""The rule parser on "what I can give" phrasings, "cheap", and lists of several wants."""

import unittest

from barter.parse import parse_rules

TEXTBOOK = ("textbook", {"course": "BIO 181"})
CHARGER = ("charger", {"connector": "usb-c"})
CALC = ("calculator", {"model": "TI-84"})
LAMP = ("furniture", {"kind": "desk lamp"})
HOODIE = ("clothing", {"kind": "hoodie"})

# text -> (have, [wants])
CASES = [
    ("need a usb-c charger, cheap; i can give my BIO 181 textbook", TEXTBOOK, [CHARGER]),
    ("I can give up my desk lamp for a usb-c charger", LAMP, [CHARGER]),
    ("need a TI-84. I can give up my BIO 181 textbook", TEXTBOOK, [CALC]),
    ("i can trade my hoodie for a TI-84", HOODIE, [CALC]),
    ("need a desk lamp, i can trade my TI-84", CALC, [LAMP]),
    ("I'll give my hoodie, need a TI-84", HOODIE, [CALC]),
    ("want a usb-c charger; i'll give my desk lamp", LAMP, [CHARGER]),
    ("I could part with my TI-84; want a desk lamp", CALC, [LAMP]),
    ("need a desk lamp, i could part with my hoodie", HOODIE, [LAMP]),
    ("offering a desk lamp, need a usb-c charger", LAMP, [CHARGER]),
    ("need a usb-c charger, offering my BIO 181 textbook", TEXTBOOK, [CHARGER]),
    ("can swap my BIO 181 textbook for a desk lamp", TEXTBOOK, [LAMP]),
    ("need a TI-84, can swap my hoodie", HOODIE, [CALC]),
]


def shape(listing):
    have = (listing.have.category, listing.have.attributes) if listing.have else None
    return have, [(w.category, w.attributes) for w in listing.wants]


class GivePhrasings(unittest.TestCase):
    def test_give_phrasings(self):
        for text, have, wants in CASES:
            with self.subTest(text=text):
                self.assertEqual(shape(parse_rules(text)), (have, wants))

    def test_the_reported_message(self):
        listing = parse_rules("need a usb-c charger, cheap; i can give my BIO 181 textbook")
        self.assertIsNotNone(listing.have)
        self.assertFalse(listing.is_giveaway)


class Cheap(unittest.TestCase):
    """'cheap' means give up as little as possible. Listing has no field for it, so it is read
    and dropped: never an item, never a floor."""

    def test_cheap_phrasings_parse_cleanly(self):
        for text in (
            "need a usb-c charger, cheap; i have a BIO 181 textbook",
            "need a usb-c charger as cheap as possible, i have a BIO 181 textbook",
            "need a usb-c charger, cheap as possible. i have a BIO 181 textbook",
            "need a cheap usb-c charger, i have a BIO 181 textbook",
            "need a usb-c charger, low cost, i have a BIO 181 textbook",
            "need a low-cost usb-c charger, i have a BIO 181 textbook",
        ):
            with self.subTest(text=text):
                listing = parse_rules(text)
                self.assertEqual(shape(listing), (TEXTBOOK, [CHARGER]))
                self.assertIsNone(listing.floor)

    def test_cheap_never_names_an_item(self):
        listing = parse_rules("need a beanbag, cheap")
        self.assertEqual([(w.category, w.attributes) for w in listing.wants], [("other", {"kind": "beanbag"})])
        self.assertIsNone(listing.have)

    def test_cheap_alone(self):
        listing = parse_rules("cheap as possible")
        self.assertIsNone(listing.have)
        self.assertEqual(listing.wants, [])

    def test_a_stated_floor_still_reads(self):
        listing = parse_rules("need a usb-c charger, cheap. I have a BIO 181 textbook, worth at least $30")
        self.assertEqual(listing.floor, 30.0)


class SeveralWants(unittest.TestCase):
    def test_a_list_becomes_several_wants_in_order(self):
        listing = parse_rules("need a TI-84, a desk lamp and a usb-c charger")
        self.assertIsNone(listing.have)
        self.assertEqual(shape(listing)[1], [CALC, LAMP, CHARGER])

    def test_a_list_with_something_to_give(self):
        listing = parse_rules("need a TI-84, a desk lamp and a usb-c charger; i can give my hoodie")
        self.assertEqual(shape(listing), (HOODIE, [CALC, LAMP, CHARGER]))

    def test_and_between_two_wants(self):
        listing = parse_rules("I have a hoodie, want a desk lamp and a TI-84")
        self.assertEqual(shape(listing), (HOODIE, [LAMP, CALC]))


if __name__ == "__main__":
    unittest.main()
