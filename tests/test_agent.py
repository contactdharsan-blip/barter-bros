import random
import unittest
from pathlib import Path

from swap import asu
from swap.agent import Offer, StudentAgent
from swap.models import Meeting, Section, TimePrefs, Want, parse_clock

FIXTURE = Path(__file__).parent / "fixtures" / "chm113-2271.json"
CATALOG = asu.load_fixture(FIXTURE)
BY_NBR = {s.class_nbr: s for s in CATALOG}

BIO = Section("90021", "BIO 181", "2101", "LEC",
              (Meeting(frozenset({"TU", "TH"}), parse_clock("10:30 AM"), parse_clock("11:45 AM")),), 300, 300, "TEMPE")
TUTH = Want("CHM 113", days_in=frozenset({"TU", "TH"}))


def agent(schedule, wants, prefs=TimePrefs(), seed=1):
    return StudentAgent("Test Student", schedule, wants, prefs, rng=random.Random(seed))


class Acceptable(unittest.TestCase):
    def test_rule_keeps_same_course_component_and_campus(self):
        a = agent([BY_NBR["10022"]], [TUTH])
        accepted, _ = a.review(TUTH, CATALOG)
        # Tempe Tu/Th lectures only: no DTPHX/WEST, no labs, no online.
        self.assertEqual([s.class_nbr for s in accepted], ["11989", "11999"])

    def test_clash_with_another_class_is_filtered(self):
        a = agent([BY_NBR["10022"], BIO], [TUTH])
        accepted, skipped = a.review(TUTH, CATALOG)
        self.assertEqual([s.class_nbr for s in accepted], ["11989"])
        self.assertEqual([s.section.class_nbr for s in skipped], ["11999"])
        self.assertIn("BIO 181", skipped[0].reason)

    def test_clash_plus_preference_means_no_offer(self):
        a = agent([BY_NBR["10022"], BIO], [TUTH], TimePrefs(not_before=parse_clock("10:00 AM")))
        self.assertIsNone(a.offer(TUTH, CATALOG))

    def test_explicit_list_still_checks_clashes(self):
        want = Want("CHM 113", sections=frozenset({"11999"}))
        a = agent([BY_NBR["10022"], BIO], [want])
        self.assertIsNone(a.offer(want, CATALOG))

    def test_rule_never_matches_online(self):
        want = Want("CHM 113", start_at_or_after=0)
        self.assertFalse(want.matches(BY_NBR["34674"]))

    def test_offer_has_only_the_four_fields(self):
        a = agent([BY_NBR["10022"], BIO], [TUTH])
        offer = a.offer(TUTH, CATALOG)
        self.assertEqual(offer.payload(), {"handle": a.handle, "course": "CHM 113", "have": "10022", "accept": ["11989"]})

    def test_exchange_rejects_extra_fields(self):
        with self.assertRaises(ValueError):
            Offer.from_payload({"handle": "h", "course": "CHM 113", "have": "1", "accept": [], "name": "x"})

    def test_handle_is_random_and_stable(self):
        a = agent([BY_NBR["10022"]], [TUTH], seed=1)
        b = agent([BY_NBR["10022"]], [TUTH], seed=2)
        self.assertNotEqual(a.handle, b.handle)
        self.assertEqual(a.offer(TUTH, CATALOG).handle, a.offer(TUTH, CATALOG).handle)
        self.assertNotIn("Test", a.handle)


if __name__ == "__main__":
    unittest.main()
