import unittest
from pathlib import Path

from swap import asu
from swap.models import Meeting, parse_clock

FIXTURE = Path(__file__).parent / "fixtures" / "chm113-2271.json"


class FixtureParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sections = asu.load_fixture(FIXTURE)
        cls.by_nbr = {s.class_nbr: s for s in cls.sections}

    def test_reads_every_row(self):
        self.assertEqual(len(self.sections), 79)

    def test_lecture_fields(self):
        s = self.by_nbr["10022"]
        self.assertEqual(s.course, "CHM 113")
        self.assertEqual(s.section, "1001")
        self.assertEqual(s.component, "LEC")
        self.assertEqual(s.campus, "TEMPE")
        self.assertEqual(s.instructor, "Kanchana Chandrakanthan")
        self.assertEqual(s.meetings, (Meeting(frozenset({"MO", "WE", "FR"}), 10 * 60 + 10, 11 * 60),))

    def test_seats_come_from_enrltot_and_enrlcap(self):
        s = self.by_nbr["10022"]
        self.assertEqual((s.seats_taken, s.capacity), (33, 386))
        self.assertTrue(s.has_open_seat)
        full = self.by_nbr["11991"]  # a Tempe lab, 48/48
        self.assertEqual((full.seats_taken, full.capacity), (48, 48))
        self.assertFalse(full.has_open_seat)

    def test_afternoon_times_and_tuth(self):
        s = self.by_nbr["22877"]  # T Th 1:30 PM - 2:45 PM
        self.assertEqual(s.meetings[0].days, frozenset({"TU", "TH"}))
        self.assertEqual((s.meetings[0].start, s.meetings[0].end), (13 * 60 + 30, 14 * 60 + 45))

    def test_component_is_the_rows_own(self):
        # SSRCOMPONENT says LEC (or nothing) on lab and recitation rows; COMPONENTPRIMARY is right.
        self.assertEqual(self.by_nbr["11428"].component, "LAB")
        self.assertEqual(self.by_nbr["11988"].component, "LAB")
        self.assertEqual(self.by_nbr["11229"].component, "REC")

    def test_online_section_has_no_meetings(self):
        s = self.by_nbr["34674"]
        self.assertEqual(s.meetings, ())
        self.assertFalse(s.has_open_seat)  # 300/300

    def test_staff_is_not_an_instructor_name(self):
        self.assertEqual(self.by_nbr["11624"].instructor, "Alan Szeto")

    def test_clock(self):
        self.assertEqual(parse_clock("12:00 PM"), 720)
        self.assertEqual(parse_clock("12:05 AM"), 5)
        self.assertEqual(parse_clock("4:30 PM"), 16 * 60 + 30)

    def test_fetch_url_is_built_without_calling(self):
        url = asu.search_url("CHM", "113", "2271")
        self.assertTrue(url.startswith(asu.API + "?refine=Y&campusOrOnlineSelection=A"))
        self.assertIn("subject=CHM&catalogNbr=113&term=2271", url)


if __name__ == "__main__":
    unittest.main()
