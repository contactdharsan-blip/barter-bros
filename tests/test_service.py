"""The live service: sessions, the chat-app API (with a fake channel), the demo flow, profiles and
when they're revealed, the busy market, and what crosses the wire.

Standard library only. Bots answer inline (delay=None) except in the tests of the timer path.
"""

import json
import time
import unittest

from barter.market import MARKET
from barter.models import OFFER_KEYS
from barter.profiles import PUBLIC_KEYS
from barter.service import SCENARIOS, ExchangeService, ServiceError
from test_barter_privacy import ATTRIBUTE_KEYS, ENVELOPE_KEYS, ITEM_KEYS, WANT_KEYS, dicts, leak, scalars

SCENARIO = "book-charger-case-3way"
PEOPLE = json.loads((SCENARIOS / f"{SCENARIO}.json").read_text())["people"] + MARKET
JORDAN = PEOPLE[0]


def demo(service=None):
    """Seed the 3-way: Jordan DMs their agent on the fake Discord; Sam, Leo and Priya are bots."""
    service = service or ExchangeService()
    seeded = service.seed_scenario(SCENARIO)
    return service, seeded["session"]


def current(service, sid):
    return service.session_view(sid)["trades"][0]


def texts(replies):
    return " ".join(r["text"] for r in replies)


def action(replies, label):
    return next(a["id"] for r in replies for a in r["actions"] if a["label"] == label)


class FakeChannel:
    """What a Discord bot or iMessage bridge does: send DMs and button presses, collect replies."""

    def __init__(self, service, channel="discord"):
        self.service, self.channel, self.pushed = service, channel, []
        service.subscribe(lambda channel, user, reply: self.pushed.append((channel, user, reply)))

    def dm(self, user, text, name=None):
        return self.service.handle_message(self.channel, user, text, name)

    def press(self, user, action_id):
        return self.service.press(self.channel, user, action_id)


class Sessions(unittest.TestCase):
    def test_a_session_has_an_id_a_handle_a_channel_and_a_profile(self):
        service = ExchangeService()
        s = service.create_session("Morgan Private", "discord")
        view = service.session_view(s.id)
        self.assertEqual((view["id"], view["handle"], view["channel"]), (s.id, s.handle, "discord"))
        self.assertEqual(view["profile"]["name"], "Morgan Private")  # your own profile, to you
        self.assertEqual(set(view["profile"]), set(PUBLIC_KEYS))
        service.post_message(s.id, "Have a desk lamp, need a usb-c charger.")
        public = json.dumps([service.exchange_view(), service.wire_view()])
        self.assertNotIn("Morgan", public)  # nobody has accepted a loop with Morgan
        self.assertTrue(s.handle.startswith("h-"))

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

    def test_profile_edits_are_trimmed_and_need_a_name(self):
        service = ExchangeService()
        s = service.create_session("Ana")
        service.update_profile(s.id, campus="ASU West", bio="x" * 500)
        self.assertEqual(len(s.profile.bio), 160)
        self.assertEqual(s.profile.campus, "ASU West")
        with self.assertRaises(ServiceError):
            service.update_profile(s.id, name="  ")


class ChannelApi(unittest.TestCase):
    """The calls a Discord bot or an iMessage bridge makes, through a fake channel."""

    def setUp(self):
        self.service = ExchangeService()
        self.service.seed_scenario(SCENARIO)  # Jordan is demo-jordan; we add a second student below
        self.fake = FakeChannel(self.service)

    def test_first_contact_makes_a_session_and_replies_to_the_dm(self):
        replies = self.fake.dm("u-1", "Have a futon. Need a rug.", name="Rae Ng")
        s = self.service.by_user[("discord", "u-1")]
        self.assertEqual((s.channel, s.profile.name), ("discord", "Rae Ng"))
        self.assertIn("futon", texts(replies))
        self.assertIn(s.handle, texts(replies))
        self.assertTrue(all(set(r) == {"id", "text", "actions"} for r in replies))
        self.assertIs(self.service.user_session("discord", "u-1"), s)  # same user, same session

    def test_the_whole_flow_by_buttons(self):
        # A fresh student who completes a 2-way with Leo (desk lamp <-> USB-C charger).
        replies = self.fake.dm("u-2", "I have a 30W USB-C charger. I need a desk lamp.")
        self.assertIn("2-way trade loop", texts(replies))
        replies = self.fake.press("u-2", action(replies, "Accept"))
        self.assertIn("now you can see who you're trading with: Leo Marsh", texts(replies))
        self.assertIn("Meetup plan", texts(replies))
        replies = self.fake.press("u-2", action(replies, "Works for me"))
        self.assertIn("Meetup agreed by all 2", texts(replies))
        replies = self.fake.press("u-2", action(replies, "Got it ✓"))
        self.assertIn("Done: you have the desk lamp", texts(replies))
        s = self.service.by_user[("discord", "u-2")]
        self.assertEqual(s.history[0]["state"], "handed_off")
        self.assertEqual((s.profile.completed, s.profile.showed_up), (1, 1))

    def test_a_stale_or_unknown_button(self):
        replies = self.fake.dm("u-3", "I have a 30W USB-C charger. I need a desk lamp.")
        accept = action(replies, "Accept")
        self.fake.press("u-3", accept)
        self.assertEqual(texts(self.fake.press("u-3", accept)), "That one's already settled.")
        with self.assertRaises(ServiceError):
            self.fake.press("u-3", "T1:dance:yes")
        with self.assertRaises(ServiceError) as e:
            self.fake.press("nobody", accept)
        self.assertEqual(e.exception.status, 404)

    def test_open_buttons_close_once_answered(self):
        sid = self.service.by_user[("discord", "demo-jordan")].id
        def open_labels():
            return [a["label"] for e in self.service.session_view(sid)["chat"] for a in e["actions"] if a["open"]]
        self.assertEqual(open_labels(), ["Accept", "Decline"])
        self.service.vote(sid, current(self.service, sid)["id"], True)
        self.assertEqual(open_labels(), ["Works for me", "Can't make it"])

    def test_replies_that_arrive_later_are_pushed(self):
        service = ExchangeService(delay=0.01)
        fake = FakeChannel(service)
        service.seed_scenario(SCENARIO)
        fake.pushed.clear()
        jordan = service.by_user[("discord", "demo-jordan")]
        fake.press("demo-jordan", f"{current(service, jordan.id)['id']}:loop:yes")
        deadline = time.time() + 3
        while time.time() < deadline and "Meetup plan" not in " ".join(r["text"] for _, _, r in fake.pushed):
            time.sleep(0.01)
        pushed = [(c, u) for c, u, r in fake.pushed if "Meetup plan" in r["text"]]
        self.assertEqual(pushed, [("discord", "demo-jordan")])  # bots get nothing pushed


class DemoFlow(unittest.TestCase):
    def test_seed_makes_jordan_a_discord_student_who_has_already_dmd(self):
        service = ExchangeService()
        seeded = service.seed_scenario(SCENARIO)
        self.assertEqual((seeded["channel"], seeded["user_ref"], seeded["bots"]), ("discord", "demo-jordan", 3))
        chat = service.session_view(seeded["session"])["chat"]
        self.assertEqual([e["text"] for e in chat if e["from"] == "you"], [JORDAN["says"]])
        self.assertEqual(len(service.exchange.trades), 1)
        self.assertGreater(len({n["channel"] for n in service.exchange_view()["nodes"]}), 1)

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
        view = service.session_view(sid)
        self.assertEqual(view["trades"][0]["state"], "handed_off")
        self.assertFalse(view["listing"]["open"])
        self.assertEqual(view["history"][0]["got"], "USB-C 20 W charger (good)")
        self.assertEqual(len(service.exchange.book), 1)  # Leo, still waiting for a charger
        kinds = [m["kind"] for m in service.wire_view()]
        for kind in ("offer", "edge?", "edge", "proposal", "vote", "identity", "free?", "free", "spot?", "spot",
                     "meetup", "plan-vote", "receipt", "done"):
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
        self.assertFalse(trade["revealed"])
        self.assertNotIn("identity", [m["kind"] for m in service.wire_view()])

    def test_listing_is_frozen_during_a_trade(self):
        service, sid = demo()
        service.post_message(sid, "Actually I have a desk lamp.")
        self.assertEqual(service.session_view(sid)["listing"]["have"]["category"], "textbook")
        self.assertIn("can't change your listing", service.session_view(sid)["chat"][-1]["text"])

    def test_a_no_show_counts_against_the_record(self):
        service = ExchangeService()
        a = service.create_session("Ann Lee")
        b = service.create_session("Ben Cho", bot=True, answers={"handoff": "noshow"})
        service.post_message(b.id, "Have a desk lamp, need a usb-c charger.")
        service.post_message(a.id, "I have a 30W USB-C charger. I need a desk lamp.")
        tid = current(service, a.id)["id"]
        service.vote(a.id, tid, True)
        service.answer_meetup(a.id, tid, True)
        self.assertEqual(current(service, a.id)["state"], "unwound")
        self.assertEqual((b.profile.no_shows, a.profile.showed_up, a.profile.no_shows), (1, 1, 0))

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


class Profiles(unittest.TestCase):
    """Before everyone accepts: handles and listings only. After: the public profile, and nothing more."""

    def setUp(self):
        self.service, self.sid = demo()
        self.me = self.service.session(self.sid)
        self.tid = current(self.service, self.sid)["id"]
        self.partners = [m["handle"] for m in current(self.service, self.sid)["members"] if not m["you"]]

    def test_no_profile_field_crosses_before_everyone_accepts(self):
        # Sam and Priya (bots) have accepted; Jordan hasn't.
        traffic = json.dumps(self.service.wire_view()).lower()
        for s in self.service.sessions.values():
            for secret in s.profile.name.split() + [s.profile.bio]:
                self.assertIsNone(leak(secret, traffic), secret)
        for key in ("profiles", "campus", "bio", "member_since", "showed_up", "no_shows", "initials"):
            self.assertNotIn(f'"{key}"', traffic)
        self.assertTrue(all(m["profile"] is None for m in current(self.service, self.sid)["members"]))
        for h in self.partners:
            with self.assertRaises(ServiceError) as e:
                self.service.profile_view(self.sid, h)
            self.assertEqual(e.exception.status, 403)
        self.assertEqual(self.service.profile_view(self.sid)["name"], "Jordan Rivera")  # your own, always

    def test_after_everyone_accepts_exactly_the_public_fields_cross(self):
        before = len(self.service.exchange.log)
        self.service.vote(self.sid, self.tid, True)
        identity = [m for m in self.service.wire_view(before) if m["kind"] == "identity"]
        self.assertEqual(sorted(m["recipient"] for m in identity), sorted([self.me.handle] + self.partners))
        for m in identity:
            self.assertEqual(set(m["payload"]), {"trade", "profiles"})
            self.assertEqual(len(m["payload"]["profiles"]), 2)
            for p in m["payload"]["profiles"]:
                self.assertEqual(list(p), list(PUBLIC_KEYS))
                self.assertNotEqual(p["handle"], m["recipient"])  # you aren't sent your own
        mine = next(m for m in identity if m["recipient"] == self.me.handle)
        self.assertEqual(sorted(p["name"] for p in mine["payload"]["profiles"]), ["Priya Shah", "Sam Okoye"])
        # identity comes after the last vote, before the meetup questions
        kinds = [m["kind"] for m in self.service.wire_view(before)]
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "vote"), kinds.index("identity"))
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "identity"), kinds.index("free?"))
        # and the companion can now open their profiles
        members = current(self.service, self.sid)["members"]
        self.assertEqual(sorted(m["profile"]["name"] for m in members if not m["you"]), ["Priya Shah", "Sam Okoye"])
        for h in self.partners:
            view = self.service.profile_view(self.sid, h)
            self.assertNotIn("history", view)
            self.assertLessEqual(set(view), set(PUBLIC_KEYS) | {"channel", "listing", "you"})

    def test_leo_never_learns_who_jordan_is(self):
        self.service.vote(self.sid, self.tid, True)
        leo = next(s for s in self.service.sessions.values() if s.profile.name == "Leo Marsh")
        with self.assertRaises(ServiceError):
            self.service.profile_view(leo.id, self.me.handle)
        self.assertNotIn(leo.handle, [m["recipient"] for m in self.service.wire_view() if m["kind"] == "identity"])


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
    """The wire log is exactly the traffic between agents: the simulator's rules, plus `identity`
    messages that may carry public profiles, and only after every member of that loop said yes."""

    @classmethod
    def setUpClass(cls):
        cls.services = []
        service, sid = demo()  # the whole demo, then the busy market
        tid = current(service, sid)["id"]
        service.vote(sid, tid, True)
        service.answer_meetup(sid, tid, True)
        service.receipt(sid, tid, True)
        service.seed_market()
        cls.services.append(service)
        service, sid = demo()  # a decline
        service.vote(sid, current(service, sid)["id"], False)
        cls.services.append(service)
        cls.logs = [m for s in cls.services for m in s.wire_view()]
        cls.plain = [m for m in cls.logs if m["kind"] != "identity"]

    def test_listings_carry_only_listing_keys(self):
        offers = 0
        for msg in self.logs:
            if msg["kind"] == "offer":
                offers += 1
                self.assertEqual(list(msg["payload"]), list(OFFER_KEYS))
            if msg["kind"] == "identity":
                self.assertEqual(set(msg["payload"]), {"trade", "profiles"})
                for p in msg["payload"]["profiles"]:
                    self.assertEqual(list(p), list(PUBLIC_KEYS))
                continue
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

    def test_contact_reason_and_calendar_never_cross_and_names_only_in_identity(self):
        everything = json.dumps(self.logs).lower()
        plain = json.dumps(self.plain).lower()
        for person in PEOPLE:
            for secret in filter(None, [person.get("contact", ""), person.get("reasons", "")] + person.get("free", [])):
                self.assertIsNone(leak(secret, everything), f"{secret!r} leaked")
            for word in person["name"].split():
                self.assertIsNone(leak(word, plain), f"{word!r} outside an identity message")

    def test_identity_only_after_every_member_said_yes(self):
        for service in self.services:
            log = service.wire_view()
            for i, m in enumerate(log):
                if m["kind"] != "identity":
                    continue
                trade = service.exchange.trades[m["payload"]["trade"]]
                yes = {x["payload"]["handle"] for x in log[:i]
                       if x["kind"] == "vote" and x["payload"]["trade"] == trade.id and x["payload"]["answer"] == "yes"}
                self.assertEqual(yes, set(trade.handles))

    def test_floors_and_private_values_never_cross(self):
        private = set()
        for service in self.services:
            for s in service.sessions.values():
                if s.agent.listing.floor is not None:
                    private.add(s.agent.listing.floor)
                private |= set(s.agent.values.values())
        self.assertGreaterEqual(private, {30.0, 35, 12.0})
        numbers = {v for m in self.plain for v in scalars(m["payload"]) if isinstance(v, (int, float))
                   and not isinstance(v, bool)}
        self.assertEqual(numbers & private, set())
        text = json.dumps([m["payload"] for m in self.logs]).lower()
        self.assertNotIn("$", text)
        for word in ("floor", "value", "worth", "price", "contact"):
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

    def test_the_judge_view_is_anonymous(self):
        for service in self.services:
            view = json.dumps(service.exchange_view()).lower()
            for person in PEOPLE:
                for secret in filter(None, person["name"].split() + [person.get("contact", ""), person.get("reasons", "")]):
                    self.assertIsNone(leak(secret, view), f"{secret!r} in the exchange view")
            for word in ("floor", "worth", "$", '"bio"', '"campus"', '"profiles"'):
                self.assertNotIn(word, view)


if __name__ == "__main__":
    unittest.main()
