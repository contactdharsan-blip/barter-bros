"""What a student can say beyond a listing: "can't make it" (the agents move the meetup), when
they're free, a message to their trade partners, "cancel", "help", and the giveaway question.

Two humans on a fake Discord, no bots, so every answer is a press. Standard library only.
"""

import json
import unittest

from barter.meetup import Block, parse_availability
from barter.service import HELP, ExchangeService
from test_barter_privacy import ENVELOPE_KEYS, dicts, leak

A_SAYS = "I have a TI-84, need a desk chair"
B_SAYS = "I have a desk chair, need a TI-84"


def texts(replies):
    return " ".join(r["text"] for r in replies)


def labels(replies):
    return [a["label"] for r in replies for a in r["actions"]]


class Pair(unittest.TestCase):
    """A ("1") and B ("2") match on a 2-way; `accepted()` takes them to the meetup question."""

    def setUp(self):
        self.service = ExchangeService()
        self.pushed = []
        self.service.subscribe(lambda channel, user, reply: self.pushed.append((user, reply)))

    def dm(self, user, text, name=None):
        return self.service.handle_message("discord", user, text, name)

    def press(self, user, action_id):
        return self.service.press("discord", user, action_id)

    def to(self, user):
        return " ".join(r["text"] for u, r in self.pushed if u == user)

    def trade(self):
        return list(self.service.exchange.trades.values())[-1]

    def accepted(self):
        self.dm("1", A_SAYS, "Ana Reyes")
        self.dm("2", B_SAYS, "Ben Cho")
        t = self.trade()
        self.press("1", f"{t.id}:loop:yes")
        self.press("2", f"{t.id}:loop:yes")
        return t

    def meetup(self, t, yes):
        """The id on the newest meetup plan's button."""
        return f"{t.id}:meetup:{t.plan_round}:{'yes' if yes else 'no'}"

    def kinds(self):
        return [m["kind"] for m in self.service.wire_view()]


class MeetupMoves(Pair):
    def test_cant_make_it_moves_the_meetup_instead_of_ending_the_trade(self):
        t = self.accepted()
        self.assertEqual(t.plan.slot.label(), "Sat 10:00 AM-10:30 AM")
        self.pushed.clear()
        replies = self.press("2", self.meetup(t, False))
        self.assertEqual(t.state.value, "accepted_by_all")
        self.assertEqual(t.plan.slot.label(), "Sat 10:30 AM-11:00 AM")
        self.assertIn("You can't make Sat 10:00 AM-10:30 AM", texts(replies))
        self.assertEqual(labels(replies), ["Works for me", "Can't make it"])
        self.assertIn("Ben Cho can't make Sat 10:00 AM-10:30 AM", self.to("1"))
        self.assertNotIn(t.loop, self.service.exchange.declined)
        # On the wire: B's no names the slot, B's agent says no to it when asked again, a new meetup.
        log = self.service.wire_view()
        no = next(i for i, m in enumerate(log) if m["kind"] == "plan-vote" and m["payload"]["answer"] == "no")
        self.assertEqual(log[no]["payload"]["slot"], "Sat 10:00 AM-10:30 AM")
        after = log[no + 1:]
        b = self.service.by_user[("discord", "2")].handle
        self.assertIn(("free", b, "Sat 10:00 AM-10:30 AM", "no"),
                      [(m["kind"], m["sender"], m["payload"].get("slot"), m["payload"].get("answer")) for m in after])
        self.assertEqual([m["payload"]["slot"] for m in after if m["kind"] == "meetup"], ["Sat 10:30 AM-11:00 AM"] * 2)
        self.press("1", self.meetup(t, True))
        self.press("2", self.meetup(t, True))
        self.assertEqual(t.state.value, "meetup_agreed")

    def test_an_old_plans_button_does_not_say_yes_to_the_new_time(self):
        self.dm("1", A_SAYS)
        self.dm("2", B_SAYS)
        t = self.trade()
        self.press("1", f"{t.id}:loop:yes")
        replies = self.press("2", f"{t.id}:loop:yes")
        old_yes = next(a["id"] for r in replies for a in r["actions"] if a["label"] == "Works for me")
        self.press("1", self.meetup(t, False))  # the plan moves to 10:30
        self.assertEqual(t.plan.slot.label(), "Sat 10:30 AM-11:00 AM")
        replies = self.press("2", old_yes)  # B taps the first plan's button
        self.assertEqual(texts(replies), "That time changed; use the newest plan.")
        self.assertEqual(t.plan_votes, {})
        sid = self.service.by_user[("discord", "2")].id
        opened = [a["id"] for e in self.service.session_view(sid)["chat"] for a in e["actions"] if a["open"]]
        self.assertEqual(opened, [self.meetup(t, True), self.meetup(t, False)])
        self.press("2", self.meetup(t, True))
        self.assertIn(self.service.by_user[("discord", "2")].handle, t.plan_votes)

    def test_it_unwinds_only_when_no_slot_is_left_and_can_form_again(self):
        t = self.accepted()
        refusals = 0
        while t.state.value == "accepted_by_all":
            self.press("2", self.meetup(t, False))
            refusals += 1
        self.assertEqual(refusals, len(self.service.window.slots()))
        self.assertEqual(t.state.value, "unwound")
        self.assertTrue(t.stalled)
        self.assertNotIn(t.loop, self.service.exchange.declined)
        self.assertIn("I'll try this trade again", self.to("1"))
        self.assertEqual(len(self.service.exchange.trades), 1)  # not re-proposed while nothing changed
        self.dm("2", "free sunday 1-3pm")  # B's times change: the same pair forms again
        again = self.trade()
        self.assertNotEqual(again.id, t.id)
        self.assertEqual(set(again.handles), set(t.handles))

    def test_a_no_to_the_loop_still_ends_it_for_good(self):
        self.dm("1", A_SAYS)
        self.dm("2", B_SAYS)
        t = self.trade()
        self.press("2", f"{t.id}:loop:no")
        self.assertIn(t.loop, self.service.exchange.declined)
        self.dm("1", A_SAYS)
        self.assertEqual(len(self.service.exchange.trades), 1)


class FreeTimes(Pair):
    def test_phrases(self):
        self.assertEqual(parse_availability("I'm only free sunday afternoon")[0], [Block("SU", 12 * 60, 17 * 60)])
        self.assertEqual(parse_availability("free sat 1-3pm")[0], [Block("SA", 13 * 60, 15 * 60)])
        self.assertEqual(parse_availability("available sat or sun after 2")[0],
                         [Block("SA", 14 * 60, 24 * 60), Block("SU", 14 * 60, 24 * 60)])
        for text in ("Free couch, come grab it", "Moving out Sunday, giving away a desk for free.",
                     "running 10 min late, red shirt", "I have a TI-84, need a desk chair"):
            self.assertIsNone(parse_availability(text), text)

    def test_mid_trade_free_times_are_used_not_bounced(self):
        t = self.accepted()
        replies = self.dm("2", "I'm only free sunday afternoon, can we do 2pm?")
        self.assertNotIn("can't change your listing", texts(replies))
        self.assertIn("you're free Sun afternoon (12:00 PM-5:00 PM)", texts(replies))
        self.assertEqual(t.plan.slot.label(), "Sun 12:00 PM-12:30 PM")
        self.assertIn("the times you gave me", texts(replies))
        self.assertNotIn("asked your calendar", self.to("1") + texts(replies))
        self.assertIn("You haven't told me when you're free", self.to("1"))

    def test_free_times_stay_private(self):
        self.dm("2", "free sat 1-3pm")
        self.accepted()
        self.assertEqual(self.trade().plan.slot.label(), "Sat 1:00 PM-1:30 PM")
        wire = json.dumps(self.service.wire_view()).lower()
        for said in ("1-3pm", "1:00 pm-3:00 pm", "free sat"):
            self.assertNotIn(said, wire)

    def test_free_times_with_no_listing_just_say_so(self):
        replies = self.dm("1", "free sat 1-3pm")
        self.assertIn("Got it: you're free Sat 1:00 PM-3:00 PM", texts(replies))
        self.assertNotIn("couldn't find", texts(replies))


class Relay(Pair):
    def test_after_the_reveal_a_message_reaches_the_others_by_name(self):
        t = self.accepted()
        self.pushed.clear()
        replies = self.dm("1", "running 10 min late, red shirt")
        self.assertEqual(texts(replies), "Passed that on to Ben Cho.")
        self.assertEqual(self.to("2"), f"Ana Reyes ({t.id}): running 10 min late, red shirt")
        relay = [m for m in self.service.wire_view() if m["kind"] == "relay"]
        a, b = (self.service.by_user[("discord", u)].handle for u in "12")
        self.assertEqual([(m["sender"], m["recipient"], m["payload"]) for m in relay], [(a, b, {"trade": t.id})])
        self.assertNotIn("red shirt", json.dumps(self.service.wire_view()))

    def test_contact_details_are_left_out(self):
        self.accepted()
        self.pushed.clear()
        replies = self.dm("1", "text me at 480-555-0117 or ana@asu.edu")
        self.assertNotIn("480", self.to("2"))
        self.assertNotIn("asu.edu", self.to("2"))
        self.assertIn("left out the contact details", texts(replies))

    def test_never_before_everyone_accepts(self):
        self.dm("1", A_SAYS, "Ana Reyes")
        self.dm("2", B_SAYS, "Ben Cho")
        self.pushed.clear()
        replies = self.dm("1", "running late")
        self.assertIn("can't change your listing", texts(replies))
        self.assertEqual(self.to("2"), "")
        self.assertNotIn("relay", self.kinds())

    def test_relay_carries_only_envelope_keys(self):
        self.accepted()
        self.dm("1", "see you there")
        for m in self.service.wire_view():
            for _, d in dicts(m["payload"]):
                if m["kind"] in ("relay", "withdraw", "plan-vote"):
                    self.assertLessEqual(set(d), ENVELOPE_KEYS)
        for name in ("Ana", "Reyes", "Ben", "Cho"):
            plain = json.dumps([m for m in self.service.wire_view() if m["kind"] != "identity"]).lower()
            self.assertIsNone(leak(name, plain))


class Withdraw(Pair):
    def test_cancel_takes_the_listing_down(self):
        self.dm("1", A_SAYS)
        handle = self.service.by_user[("discord", "1")].handle
        for text in ("cancel my listing", "stop", "remove me", "never mind, I sold it", "cancel"):
            self.dm("1", A_SAYS)
            replies = self.dm("1", text)
            self.assertIn("your listing is down", texts(replies), text)
            self.assertNotIn(handle, self.service.exchange.book)
        self.assertIn("withdraw", self.kinds())
        self.assertIn("nothing to take down", texts(self.dm("1", "cancel")))
        self.dm("2", B_SAYS)
        self.assertEqual(self.service.exchange.trades, {})  # nobody to trade with now

    def test_cancel_while_a_trade_waits_declines_it(self):
        self.dm("1", A_SAYS)
        self.dm("2", B_SAYS)
        t = self.trade()
        self.dm("1", "cancel")
        self.assertEqual(t.state.value, "unwound")
        self.assertIn("said no", self.to("2"))
        self.assertNotIn(self.service.by_user[("discord", "1")].handle, self.service.exchange.book)

    def test_cancel_then_relist_does_not_revive_a_declined_loop(self):
        self.dm("1", A_SAYS)
        self.dm("2", B_SAYS)
        self.dm("1", "cancel")  # declines T1 for both: B is told it won't be proposed again
        self.dm("1", A_SAYS)  # relisted under a new place in the queue
        self.assertEqual(len(self.service.exchange.trades), 1)

    def test_cancel_after_everyone_accepted_is_refused_not_relayed(self):
        t = self.accepted()
        self.pushed.clear()
        replies = self.dm("1", "cancel")
        self.assertIn("can't take your listing down mid-trade", texts(replies))
        self.assertEqual(t.state.value, "accepted_by_all")
        self.assertEqual(self.to("2"), "")

    def test_stop_by_the_entrance_is_a_message_not_a_cancel(self):
        self.accepted()
        self.dm("1", "stop by the entrance, I'll be there")
        self.assertIn("stop by the entrance", self.to("2"))

    def test_help(self):
        self.assertEqual(texts(self.dm("1", "help")), HELP)


class GiveawayQuestion(Pair):
    def test_an_item_with_no_wants_asks_before_listing(self):
        replies = self.dm("1", "I have a desk chair")
        self.assertEqual(labels(replies), ["Give it away", "I want something for it"])
        handle = self.service.by_user[("discord", "1")].handle
        self.assertNotIn(handle, self.service.exchange.book)
        self.assertNotIn("offer", self.kinds())
        give = replies[-1]["actions"][0]["id"]
        replies = self.press("1", give)
        self.assertIn("a giveaway", texts(replies))
        self.assertIn(handle, self.service.exchange.book)
        self.assertEqual(texts(self.press("1", give)), "That one's already settled.")

    def test_i_want_something_then_what(self):
        replies = self.dm("1", "I have a desk chair")
        self.press("1", replies[-1]["actions"][1]["id"])
        self.dm("1", "I want a TI-84")
        listing = self.service.by_user[("discord", "1")].agent.listing
        self.assertEqual((listing.have.category, [w.category for w in listing.wants]), ("furniture", ["calculator"]))

    def test_saying_free_lists_it_at_once(self):
        for text in ("Free desk chair, come grab it", "giving away my desk chair", "I have a desk chair, don't need anything"):
            with self.subTest(text=text):
                service = ExchangeService()
                replies = service.handle_message("discord", "9", text)
                self.assertEqual(labels(replies), [])
                self.assertIn("a giveaway", texts(replies))


if __name__ == "__main__":
    unittest.main()
