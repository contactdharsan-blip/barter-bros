"""The live service: sessions, the demo flow, the busy market, and what crosses the wire.

Standard library only. Bots answer inline (delay=None) except in the one test of the timer path.
"""

import json
import time
import unittest

from barter.market import MARKET
from barter.models import OFFER_KEYS
from barter.service import SCENARIOS, ExchangeService, ServiceError
from test_barter_privacy import ATTRIBUTE_KEYS, ENVELOPE_KEYS, ITEM_KEYS, WANT_KEYS, dicts, leak, scalars

SCENARIO = "book-charger-case-3way"
JORDAN = ("I have a BIO 181 textbook I'll never open again. I need a USB-C charger, fast charging if possible. "
          "I'd give the textbook for anything worth at least $30 to me.")


def demo(service=None):
    service = service or ExchangeService()
    seeded = service.seed_scenario(SCENARIO)
    service.post_message(seeded["session"], seeded["suggested"])
    return service, seeded["session"]


def current(service, sid):
    return service.session_view(sid)["trades"][0]


class Sessions(unittest.TestCase):
    def test_a_session_has_an_id_a_handle_and_a_channel_but_its_name_stays_inside(self):
        service = ExchangeService()
        s = service.create_session("Morgan Private", "discord")
        view = service.session_view(s.id)
        self.assertEqual((view["id"], view["handle"], view["channel"]), (s.id, s.handle, "discord"))
        self.assertTrue(s.handle.startswith("h-"))
        service.post_message(s.id, "Have a desk lamp, need a usb-c charger.")
        everything = json.dumps([service.session_view(s.id), service.exchange_view(), service.wire_view()])
        self.assertNotIn("Morgan", everything)
        self.assertNotIn("Private", everything)

    def test_unknown_channel_and_session_are_refused(self):
        service = ExchangeService()
        with self.assertRaises(ServiceError):
            service.create_session("X", "fax")
        with self.assertRaises(ServiceError) as e:
            service.session_view("nope")
        self.assertEqual(e.exception.status, 404)

    def test_a_message_is_parsed_explained_and_posted(self):
        service = ExchangeService()
        s = service.create_session()
        service.post_message(s.id, "I have a TI-84 calculator. I need a desk lamp.")
        view = service.session_view(s.id)
        self.assertEqual(view["listing"]["have"]["category"], "calculator")
        self.assertEqual([w["category"] for w in view["listing"]["wants"]], ["furniture"])
        self.assertTrue(view["listing"]["open"])
        said = " ".join(m["text"] for m in view["chat"] if m["from"] == "agent")
        self.assertIn("rules parser", said)
        self.assertIn(s.handle, said)
        [offer] = [m for m in service.wire_view() if m["kind"] == "offer"]
        self.assertEqual(list(offer["payload"]), list(OFFER_KEYS))

    def test_a_floor_on_its_own_updates_the_listing(self):
        service = ExchangeService()
        s = service.create_session()
        service.post_message(s.id, "I have a TI-84 calculator. I need a desk lamp.")
        service.post_message(s.id, "I'd take anything worth at least $20 for it")
        self.assertEqual(service.session_view(s.id)["listing"]["floor"], 20.0)
        self.assertEqual(len([m for m in service.wire_view() if m["kind"] == "offer"]), 1)  # the floor never crosses

    def test_nonsense_gets_a_hint_and_posts_nothing(self):
        service = ExchangeService()
        s = service.create_session()
        service.post_message(s.id, "hello?")
        self.assertEqual(service.wire_view(), [])
        self.assertIn("couldn't find", service.session_view(s.id)["chat"][-1]["text"])


class DemoFlow(unittest.TestCase):
    def test_seed_makes_jordan_human_and_the_others_bots(self):
        service = ExchangeService()
        seeded = service.seed_scenario(SCENARIO)
        self.assertEqual(seeded["suggested"], JORDAN)
        self.assertEqual(seeded["bots"], 3)
        self.assertEqual(len(service.exchange.book), 3)  # Sam, Leo and Priya have posted; Jordan hasn't
        self.assertEqual(service.exchange.trades, {})
        channels = {n["channel"] for n in service.exchange_view()["nodes"]}
        self.assertGreater(len(channels), 1)

    def test_loop_meetup_got_it_done(self):
        service, sid = demo()
        trade = current(service, sid)
        self.assertEqual((trade["shape"], trade["state"], len(trade["members"])), ("cycle", "proposed", 3))
        self.assertEqual([m["vote"] for m in trade["members"] if not m["you"]], [True, True])  # the bots accepted
        mine = trade["mine"]
        self.assertEqual(mine["gives"]["category"], "textbook")
        self.assertEqual(mine["gets"]["attributes"], {"connector": "usb-c", "watts": 20})
        self.assertEqual(mine["worth"], {"value": 35, "floor": 30.0})  # Jordan's private values, only in Jordan's view

        service.vote(sid, trade["id"], True)
        trade = current(service, sid)
        self.assertEqual(trade["state"], "accepted_by_all")
        self.assertEqual(trade["plan"], {"slot": "Sat 11:00 AM-11:30 AM", "spot": "Noble Library"})
        self.assertEqual([m["plan_vote"] for m in trade["members"] if not m["you"]], [True, True])

        service.answer_meetup(sid, trade["id"], True)
        trade = current(service, sid)
        self.assertEqual(trade["state"], "meetup_agreed")
        self.assertEqual([m["receipt"] for m in trade["members"] if not m["you"]], [True, True])

        service.receipt(sid, trade["id"], True)
        trade = current(service, sid)
        self.assertEqual(trade["state"], "handed_off")
        self.assertFalse(service.session_view(sid)["listing"]["open"])
        self.assertEqual(len(service.exchange.book), 1)  # Leo, still waiting for a charger
        kinds = [m["kind"] for m in service.wire_view()]
        for kind in ("offer", "edge?", "edge", "proposal", "vote", "free?", "free", "spot?", "spot", "meetup",
                     "plan-vote", "receipt", "done"):
            self.assertIn(kind, kinds)

    def test_answers_out_of_turn_are_refused(self):
        service, sid = demo()
        tid = current(service, sid)["id"]
        with self.assertRaises(ServiceError) as e:
            service.receipt(sid, tid, True)
        self.assertEqual(e.exception.status, 409)
        service.vote(sid, tid, True)
        with self.assertRaises(ServiceError):
            service.vote(sid, tid, True)
        with self.assertRaises(ServiceError) as e:
            service.vote(sid, "T99", True)
        self.assertEqual(e.exception.status, 404)

    def test_decline_unwinds_and_relists_everyone(self):
        service, sid = demo()
        service.vote(sid, current(service, sid)["id"], False)
        trade = current(service, sid)
        self.assertEqual(trade["state"], "unwound")
        self.assertEqual(len(service.exchange.book), 4)
        self.assertEqual(service.exchange.locked, set())
        self.assertEqual(len(service.exchange.trades), 1)  # that exact loop is never proposed again
        self.assertIn("said no", trade["reason"])

    def test_listing_is_frozen_during_a_trade(self):
        service, sid = demo()
        service.post_message(sid, "Actually I have a desk lamp.")
        self.assertEqual(service.session_view(sid)["listing"]["have"]["category"], "textbook")
        self.assertIn("can't change your listing", service.session_view(sid)["chat"][-1]["text"])

    def test_bots_answer_on_a_timer_when_there_is_a_delay(self):
        service, sid = demo(ExchangeService(delay=0.01))
        deadline = time.time() + 3
        while time.time() < deadline and None in [m["vote"] for m in current(service, sid)["members"] if not m["you"]]:
            time.sleep(0.01)
        self.assertEqual([m["vote"] for m in current(service, sid)["members"] if not m["you"]], [True, True])

    def test_unknown_or_section_scenarios_are_refused(self):
        service = ExchangeService()
        for name in ("nope", "../PLAN", "mixed-with-section"):
            with self.assertRaises(ServiceError):
                service.seed_scenario(name)


class Market(unittest.TestCase):
    def test_the_market_forms_two_loops_and_a_chain_and_leaves_four_waiting(self):
        service = ExchangeService()
        service.seed_market()
        view = service.exchange_view()
        self.assertEqual(view["stats"], {"agents": 12, "listings": 12, "loops": 3})
        shapes = sorted((t["shape"], len(t["members"]), t["state"]) for t in view["trades"])
        self.assertEqual(shapes, [("chain", 3, "meetup_agreed"), ("cycle", 2, "meetup_agreed"),
                                  ("cycle", 3, "meetup_agreed")])
        self.assertEqual(sum(n["status"] == "listed" for n in view["nodes"]), 4)
        self.assertEqual(len({n["channel"] for n in view["nodes"]}), 3)

    def test_market_is_seeded_once_and_leaves_the_demo_loop_alone(self):
        service, sid = demo()
        service.seed_market()
        service.seed_market()
        self.assertEqual(len(service.sessions), 4 + 12)
        self.assertEqual(current(service, sid)["state"], "proposed")
        self.assertEqual(len(current(service, sid)["members"]), 3)


class Updates(unittest.TestCase):
    def test_global_stream_sends_the_wire_then_the_exchange_then_nothing_new(self):
        service, _ = demo()
        events, cursor = service.updates({})
        kinds = [e for e, _ in events]
        self.assertEqual(kinds.count("wire"), len(service.exchange.log))
        self.assertEqual(kinds[-1], "exchange")
        self.assertEqual(service.updates(cursor)[0], [])
        service.reset()
        events, cursor = service.updates(cursor)
        self.assertEqual(events[0][0], "reset")

    def test_session_stream_sends_your_view_when_it_changes(self):
        service, sid = demo()
        events, cursor = service.updates({}, sid)
        self.assertEqual([e for e, _ in events], ["session"])
        self.assertEqual(service.updates(cursor, sid)[0], [])
        service.vote(sid, current(service, sid)["id"], True)
        [(event, view)] = service.updates(cursor, sid)[0]
        self.assertEqual(view["trades"][0]["state"], "accepted_by_all")
        service.reset()
        self.assertEqual([e for e, _ in service.updates(cursor, sid)[0]], ["reset", "gone"])


class WirePrivacy(unittest.TestCase):
    """The service's wire log is exactly the traffic between agents: the same rules as the simulator's."""

    @classmethod
    def setUpClass(cls):
        cls.people = json.loads((SCENARIOS / f"{SCENARIO}.json").read_text())["people"]
        cls.people += MARKET
        cls.services = []
        # The whole demo, a decline, and the busy market.
        service, sid = demo()
        tid = current(service, sid)["id"]
        service.vote(sid, tid, True)
        service.answer_meetup(sid, tid, True)
        service.receipt(sid, tid, True)
        service.seed_market()
        cls.services.append(service)
        service, sid = demo()
        service.vote(sid, current(service, sid)["id"], False)
        cls.services.append(service)
        cls.logs = [m for s in cls.services for m in s.wire_view()]

    def test_listings_carry_only_listing_keys(self):
        offers = 0
        for msg in self.logs:
            if msg["kind"] == "offer":
                offers += 1
                self.assertEqual(list(msg["payload"]), list(OFFER_KEYS))
            for parent, d in dicts(msg["payload"]):
                if "wants" in d:
                    self.assertEqual(set(d), set(OFFER_KEYS), msg["kind"])
                elif parent in ("have", "item"):
                    self.assertEqual(set(d), ITEM_KEYS, msg["kind"])
                elif parent == "wants":
                    self.assertEqual(set(d), WANT_KEYS, msg["kind"])
                elif parent == "attributes":
                    self.assertLessEqual(set(d), ATTRIBUTE_KEYS, msg["kind"])
                else:
                    self.assertLessEqual(set(d), ENVELOPE_KEYS, f"{msg['kind']} {sorted(d)}")
        self.assertGreater(offers, 15)

    def test_no_name_contact_reason_or_calendar_crosses(self):
        traffic = json.dumps(self.logs).lower()
        for person in self.people:
            secrets = person["name"].split() + [person.get("contact", ""), person.get("reasons", "")]
            secrets += person.get("free", [])
            for secret in filter(None, secrets):
                self.assertIsNone(leak(secret, traffic), f"{secret!r} leaked")

    def test_floors_and_private_values_never_cross(self):
        private = set()
        for service in self.services:
            for s in service.sessions.values():
                if s.agent.listing.floor is not None:
                    private.add(s.agent.listing.floor)
                private |= set(s.agent.values.values())
        self.assertGreaterEqual(private, {30.0, 35, 12.0})
        numbers = {v for m in self.logs for v in scalars(m["payload"]) if isinstance(v, (int, float))
                   and not isinstance(v, bool)}
        self.assertEqual(numbers & private, set())
        text = json.dumps([m["payload"] for m in self.logs]).lower()
        self.assertNotIn("$", text)
        for word in ("floor", "value", "worth", "price"):
            self.assertNotIn(word, text)

    def test_only_handles_and_the_exchange_send_or_receive(self):
        for service in self.services:
            handles = set(service.by_handle)
            for m in service.wire_view():
                self.assertLessEqual({m["sender"], m["recipient"]}, handles | {"exchange"})

    def test_answers_are_bare(self):
        for m in self.logs:
            if m["kind"] in ("edge", "free", "spot", "vote", "plan-vote"):
                self.assertIn(m["payload"]["answer"], ("yes", "no"))
            if m["kind"] == "receipt":
                self.assertIn(m["payload"]["answer"], ("got it", "didn't get it"))

    def test_the_judge_view_is_as_anonymous_as_the_wire(self):
        for service in self.services:
            view = json.dumps(service.exchange_view()).lower()
            for person in self.people:
                for secret in filter(None, person["name"].split() + [person.get("contact", ""), person.get("reasons", "")]):
                    self.assertIsNone(leak(secret, view), f"{secret!r} in the exchange view")
            for word in ("floor", "worth", "$"):
                self.assertNotIn(word, view)


if __name__ == "__main__":
    unittest.main()
