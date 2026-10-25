"""The Discord bot against fakes: no network, no Keychain, and no discord.py except in the wiring tests."""

import asyncio
import contextlib
import importlib.util
import io
import json
import subprocess
import threading
import time
import unittest
from types import SimpleNamespace as NS

from barter.platforms.discord_bot import (ADD_TOKEN, GUILD_HINT, STALE, Adapter, keychain_token, main,
                                          profile_embed, render)
from barter.profiles import PUBLIC_KEYS
from barter.service import ExchangeService

HAS_DISCORD = importlib.util.find_spec("discord") is not None
SCENARIO = "book-charger-case-3way"


def run(coro):
    return asyncio.run(coro)


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})


class FakeDiscord:
    """Users by id, each with a DM channel; `fetch_user` is what the adapter calls for a later reply."""

    def __init__(self):
        self.dms: dict[int, FakeChannel] = {}
        self.fetched: list[int] = []

    def dm(self, user_id: int) -> FakeChannel:
        return self.dms.setdefault(user_id, FakeChannel())

    async def fetch_user(self, user_id: int):
        self.fetched.append(user_id)
        return self.dm(user_id)


def dm_message(user_id, text, name="Rae Ng", channel=None):
    return NS(author=NS(id=user_id, display_name=name, bot=False), guild=None, mentions=[], content=text,
              channel=channel or FakeChannel())


class Press:
    """A button press: records the disable and any private note."""

    def __init__(self):
        self.disabled, self.notes = 0, []

    async def disable(self):
        self.disabled += 1

    async def notify(self, text):
        self.notes.append(text)


def buttons(messages, label):
    return next(b["custom_id"] for m in messages for b in m["buttons"] if b["label"] == label)


def contents(messages):
    return " ".join(m["content"] for m in messages)


class Rendering(unittest.TestCase):
    def test_actions_become_buttons_with_the_action_ids(self):
        reply = {"id": 3, "text": "Do you want this trade?",
                 "actions": [{"id": "T1:loop:yes", "label": "Accept", "style": "primary"},
                             {"id": "T1:loop:no", "label": "Decline", "style": "secondary"}]}
        m = render(reply)
        self.assertEqual(m["content"], "Do you want this trade?")
        self.assertEqual(m["buttons"], [{"custom_id": "T1:loop:yes", "label": "Accept", "style": "primary"},
                                        {"custom_id": "T1:loop:no", "label": "Decline", "style": "secondary"}])
        self.assertEqual(m["embeds"], [])

    def test_profile_embeds_read_only_public_fields(self):
        public = {"handle": "h-abc123", "name": "Priya Shah", "initials": "PS", "color": "purple",
                  "campus": "ASU Tempe · Barrett", "bio": "Always on time.", "member_since": "Aug 2026",
                  "completed": 6, "showed_up": 6, "no_shows": 1}
        leaky = {**public, "contact": "priya@example.edu", "reasons": "Moving out", "floor": 30, "channel": "web"}
        e = profile_embed(leaky)
        self.assertEqual(e, profile_embed(public))
        blob = json.dumps(e, ensure_ascii=False)
        for secret in ("priya@example.edu", "Moving out", "web"):
            self.assertNotIn(secret, blob)
        self.assertEqual((e["title"], e["description"], e["color"]), ("Priya Shah", "Always on time.", 0x9B5CFF))
        for shown in ("PS", "h-abc123", "ASU Tempe · Barrett", "Aug 2026", "6 showed up · 1 no-show"):
            self.assertIn(shown, blob)

    def test_reveal_reply_renders_one_embed_per_partner(self):
        service = ExchangeService()
        service.seed_scenario(SCENARIO)
        jordan = service.by_user[("discord", "demo-jordan")]
        replies = service.press("discord", "demo-jordan", f"{service.session_view(jordan.id)['trades'][0]['id']}:loop:yes")
        [reveal] = [r for r in replies if "profiles" in r]
        m = render(reveal)
        self.assertEqual([e["title"] for e in m["embeds"]], [p["name"] for p in reveal["profiles"]])
        self.assertEqual(len(m["embeds"]), 2)
        for p in reveal["profiles"]:
            self.assertEqual(set(p), set(PUBLIC_KEYS))


class Conversation(unittest.TestCase):
    def setUp(self):
        self.service = ExchangeService()
        self.service.seed_scenario(SCENARIO)  # Leo (a bot) waits with a desk lamp, wanting a USB-C charger
        self.discord = FakeDiscord()
        self.adapter = Adapter(self.service, self.discord.fetch_user)

    def test_a_dm_gets_replies_with_buttons_named_by_action_id(self):
        async def go():
            self.adapter.start()
            msg = dm_message(222, "I have a 30W USB-C charger. I need a desk lamp.", channel=self.discord.dm(222))
            await self.adapter.on_message(msg, me=NS(id=1))
            await self.adapter.flush()
        run(go())
        sent = self.discord.dm(222).sent
        self.assertIn("2-way trade loop", contents(sent))
        s = self.service.by_user[("discord", "222")]
        self.assertEqual(s.profile.name, "Rae Ng")
        offer = next(m for m in sent if m["buttons"])
        action_ids = [a["id"] for a in s.chat[-1]["actions"]]
        self.assertEqual([b["custom_id"] for b in offer["buttons"]], action_ids)
        self.assertEqual([b["label"] for b in offer["buttons"]], ["Accept", "Decline"])
        self.assertEqual(len([m for m in sent if "2-way trade loop" in m["content"]]), 1)  # each reply once
        self.assertEqual(self.discord.fetched, [])  # it answered in the DM channel it already had

    def test_a_press_calls_the_service_and_disables_the_buttons(self):
        calls = []
        real = self.service.press
        self.service.press = lambda *a: calls.append(a) or real(*a)
        press = Press()

        async def go():
            self.adapter.start()
            await self.adapter.on_message(dm_message(222, "I have a 30W USB-C charger. I need a desk lamp.",
                                                     channel=self.discord.dm(222)), None)
            await self.adapter.flush()
            accept = buttons(self.discord.dm(222).sent, "Accept")
            await self.adapter.on_press(222, accept, press.disable, press.notify)
            await self.adapter.flush()
            return accept
        accept = run(go())
        self.assertEqual(calls, [("discord", "222", accept)])
        self.assertEqual(press.disabled, 1)
        self.assertEqual(press.notes, [])
        self.assertIn("now you can see who you're trading with: Leo Marsh", contents(self.discord.dm(222).sent))

    def test_a_press_from_a_stranger_is_answered_privately(self):
        press = Press()

        async def go():
            self.adapter.start()
            await self.adapter.on_press(999, "T1:loop:yes", press.disable, press.notify)
        run(go())
        self.assertEqual((press.disabled, press.notes), (1, [STALE]))

    def test_a_press_on_someone_elses_trade_says_so(self):
        self.service.handle_message("discord", "444", "I have a TI-84, need a hoodie")  # known, but not in T1
        press = Press()

        async def go():
            self.adapter.start()
            await self.adapter.on_press(444, "T1:loop:yes", press.disable, press.notify)
        run(go())
        self.assertEqual(press.notes, ["That button isn't for a trade you're in."])

    def test_guild_messages_are_ignored_unless_the_bot_is_mentioned(self):
        me = NS(id=1)
        quiet, pinged = FakeChannel(), FakeChannel()
        before = len(self.service.sessions)

        async def go():
            self.adapter.start()
            await self.adapter.on_message(NS(author=NS(id=5, display_name="X", bot=False), guild=NS(id=9),
                                             mentions=[], content="have a lamp", channel=quiet), me)
            await self.adapter.on_message(NS(author=NS(id=5, display_name="X", bot=False), guild=NS(id=9),
                                             mentions=[me], content="@bot have a lamp", channel=pinged), me)
            await self.adapter.on_message(NS(author=NS(id=6, display_name="Bot", bot=True), guild=None,
                                             mentions=[], content="I have a lamp", channel=FakeChannel()), me)
            await self.adapter.flush()
        run(go())
        self.assertEqual(quiet.sent, [])
        self.assertEqual(pinged.sent, [{"content": GUILD_HINT}])
        self.assertEqual(len(self.service.sessions), before)  # no session for any of them

    def test_an_empty_dm_gets_the_services_refusal(self):
        ch = self.discord.dm(333)

        async def go():
            self.adapter.start()
            await self.adapter.on_message(dm_message(333, "   ", channel=ch), None)
            await self.adapter.flush()
        run(go())
        self.assertIn("say something to your agent", contents(ch.sent))


class LaterReplies(unittest.TestCase):
    def test_replies_from_timer_threads_reach_the_right_user_and_demo_refs_are_skipped(self):
        service = ExchangeService(delay=0.01)  # bots answer on threading.Timer threads
        discord = FakeDiscord()
        adapter = Adapter(service, discord.fetch_user)

        async def go():
            adapter.start()
            service.seed_scenario(SCENARIO)  # Jordan is "demo-jordan": pushed replies for him go nowhere
            await asyncio.to_thread(service.handle_message, "discord", "444",
                                    "I have a 30W USB-C charger. I need a desk lamp.", "Rae Ng")
            accept = next(a["id"] for e in service.by_user[("discord", "444")].chat for a in e["actions"])
            await asyncio.to_thread(service.press, "discord", "444", accept)  # Leo answers later, on a timer
            deadline = time.time() + 3
            while time.time() < deadline and "Meetup plan" not in contents(discord.dm(444).sent):
                await asyncio.sleep(0.01)
            await adapter.flush()
        run(go())
        self.assertIn("Meetup plan", contents(discord.dm(444).sent))
        self.assertEqual(set(discord.fetched), {444})  # never "demo-jordan", never a bot
        self.assertEqual(set(discord.dms), {444})

    def test_the_listener_skips_web_and_demo_sessions(self):
        discord = FakeDiscord()
        adapter = Adapter(ExchangeService(), discord.fetch_user)
        reply = {"id": 1, "text": "hi", "actions": []}

        async def go():
            adapter.start()
            t = threading.Thread(target=lambda: [adapter.listener(*args) for args in (
                ("discord", "555", reply), ("discord", "demo-jordan", reply), ("web", None, reply),
                ("imessage", "555", reply), ("discord", None, reply))])
            t.start()
            await asyncio.to_thread(t.join)
            await asyncio.sleep(0)
            await adapter.flush()
        run(go())
        self.assertEqual(discord.fetched, [555])
        self.assertEqual(discord.dm(555).sent, [{"content": "hi", "buttons": [], "embeds": []}])

    def test_a_failed_dm_does_not_stop_delivery(self):
        class Blocked:
            async def send(self, **kwargs):
                raise RuntimeError("Forbidden")

        ok = FakeChannel()

        async def fetch(uid):
            return Blocked() if uid == 1 else ok

        adapter = Adapter(ExchangeService(), fetch)

        async def go():
            adapter.start()
            adapter.listener("discord", "1", {"id": 1, "text": "a", "actions": []})
            adapter.listener("discord", "2", {"id": 1, "text": "b", "actions": []})
            await asyncio.sleep(0)
            await adapter.flush()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run(go())
        self.assertEqual([m["content"] for m in ok.sent], ["b"])
        self.assertEqual(err.getvalue(), "couldn't DM user 1: RuntimeError\n")  # the type only, never the reply


class SlashCommands(unittest.TestCase):
    def setUp(self):
        self.service = ExchangeService()
        self.adapter = Adapter(self.service)

    def test_help_explains_the_flow(self):
        self.assertIn("Accept", self.adapter.help())
        self.assertIn("/barter profile", self.adapter.help())

    def test_profile_sets_campus_and_bio(self):
        note, embed = self.adapter.profile(777, "Rae Ng", campus="ASU West", bio="Film major.")
        self.assertIn("campus and bio", note)
        self.assertEqual((embed["title"], embed["description"]), ("Rae Ng", "Film major."))
        p = self.service.by_user[("discord", "777")].profile
        self.assertEqual((p.campus, p.bio), ("ASU West", "Film major."))

    def test_status_before_and_during_a_trade(self):
        self.assertIn("haven't told me anything", self.adapter.status(888))
        self.service.seed_scenario(SCENARIO)
        self.service.handle_message("discord", "888", "I have a 30W USB-C charger. I need a desk lamp.")
        text = self.adapter.status(888)
        self.assertIn("2-way loop: waiting for everyone to accept", text)
        self.assertIn("You get: desk lamp", text)


class FullFlow(unittest.TestCase):
    def test_two_humans_and_the_market_reach_done_through_the_bot(self):
        """Jordan's flow between two Discord students, with the twelve market bots as noise."""
        service = ExchangeService()
        service.seed_market()
        discord = FakeDiscord()
        adapter = Adapter(service, discord.fetch_user)
        jordan, sam = 101, 202

        async def press(user, label):
            p = Press()
            await adapter.on_press(user, buttons(discord.dm(user).sent, label), p.disable, p.notify)
            await adapter.flush()
            self.assertEqual((p.disabled, p.notes), (1, []))

        async def go():
            adapter.start()
            await adapter.on_message(dm_message(jordan, "I have a BIO 181 textbook. I need a USB-C charger.",
                                                "Jordan Rivera", discord.dm(jordan)), None)
            await adapter.on_message(dm_message(sam, "I have a USB-C charger, 20W. I need a BIO 181 textbook.",
                                                "Sam Okoye", discord.dm(sam)), None)
            await adapter.flush()
            [trade] = [t for t in service.exchange.trades.values()
                       if service.by_handle[t.handles[0]].user_ref in ("101", "202")]
            self.assertEqual({service.by_handle[h].user_ref for h in trade.handles}, {"101", "202"})
            for user in (jordan, sam):
                self.assertIn("2-way trade loop", contents(discord.dm(user).sent))
            await press(jordan, "Accept")
            self.assertNotIn("now you can see", contents(discord.dm(jordan).sent))  # not until Sam says yes
            await press(sam, "Accept")
            for user, partner in ((jordan, "Sam Okoye"), (sam, "Jordan Rivera")):
                reveal = next(m for m in discord.dm(user).sent if m["embeds"])
                self.assertEqual([e["title"] for e in reveal["embeds"]], [partner])
            await press(jordan, "Works for me")
            await press(sam, "Works for me")
            await press(jordan, "Got it ✓")
            await press(sam, "Got it ✓")
        run(go())
        self.assertIn("Done: you have the USB-C 20 W charger", contents(discord.dm(jordan).sent))
        self.assertIn("Done: you have the BIO 181 textbook", contents(discord.dm(sam).sent))
        self.assertEqual(discord.fetched, [])
        for user in (jordan, sam):
            s = service.by_user[("discord", str(user))]
            self.assertEqual(s.history[0]["state"], "handed_off")


class Token(unittest.TestCase):
    def test_a_missing_token_names_the_command_to_add_it(self):
        def missing(cmd, **kwargs):
            self.assertEqual(cmd[:2], ["security", "find-generic-password"])
            return subprocess.CompletedProcess(cmd, 44, stdout="", stderr="could not be found")
        with self.assertRaises(SystemExit) as e:
            keychain_token(run=missing)
        self.assertIn(ADD_TOKEN, str(e.exception.code))
        self.assertEqual(ADD_TOKEN, "security add-generic-password -a barter-bros -s discord-bot-token -w")

    def test_the_token_is_read_into_memory(self):
        found = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="tok.en\n", stderr="")
        self.assertEqual(keychain_token(run=found), "tok.en")

    def test_dry_run_connects_to_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--dry-run", "--guild", "42", "--web-port", "8787", "--market"]), 0)
        self.assertIn("42", out.getvalue())
        self.assertIn("http://127.0.0.1:8787", out.getvalue())


@unittest.skipUnless(HAS_DISCORD, "discord.py is not installed (the `discord` extra)")
class Wiring(unittest.TestCase):
    def test_buttons_are_persistent_dynamic_items_and_presses_disable_them(self):
        import discord

        from barter.platforms.discord_bot import build_bot

        service = ExchangeService()
        service.seed_scenario(SCENARIO)
        client, tree, adapter, ActionButton = build_bot(service, guild_id=42)
        self.assertEqual([c.name for c in tree.get_commands()], ["barter"])
        self.assertEqual(sorted(c.name for c in tree.get_commands()[0].commands), ["help", "profile", "status"])

        reply = {"id": 9, "text": "Do you want this trade?", "profiles": [service.by_handle[h].profile.public(h)
                                                                         for h in list(service.by_handle)[:1]],
                 "actions": [{"id": "T1:loop:yes", "label": "Accept", "style": "primary"},
                             {"id": "T1:loop:no", "label": "Decline", "style": "secondary"}]}
        async def go():  # discord.py builds views on a running loop
            kwargs = adapter.convert(render(reply))
            view = kwargs["view"]
            self.assertIsNone(view.timeout)
            self.assertTrue(view.is_persistent())
            self.assertEqual([i.custom_id for i in view.children], ["T1:loop:yes", "T1:loop:no"])
            self.assertEqual([i.item.style for i in view.children],
                             [discord.ButtonStyle.primary, discord.ButtonStyle.secondary])
            self.assertTrue(all(isinstance(i, ActionButton) for i in view.children))
            self.assertIsInstance(kwargs["embeds"][0], discord.Embed)
            self.assertTrue(ActionButton.__discord_ui_compiled_template__.fullmatch("T12:receipt:no"))
            self.assertFalse(ActionButton.__discord_ui_compiled_template__.fullmatch("T1:dance:yes"))
            self.assertTrue(ActionButton.__discord_ui_compiled_template__.fullmatch("L4:giveaway:no"))
            self.assertTrue(ActionButton.__discord_ui_compiled_template__.fullmatch("F7:times:yes"))
            self.assertTrue(ActionButton.__discord_ui_compiled_template__.fullmatch("T3:meetup:2:yes"))
            self.assertFalse(ActionButton.__discord_ui_compiled_template__.fullmatch("T3:meetup:yes"))

            # A press: the callback disables every button on the message, then calls the service.
            edits, pressed = [], []

            async def edit_message(**kw):
                edits.append([c.disabled for c in kw["view"].children])

            async def on_press(user_id, action_id, disable, notify):
                pressed.append((user_id, action_id))
                await disable()

            adapter.on_press = on_press
            interaction = NS(user=NS(id=7), response=NS(edit_message=edit_message), followup=NS(send=None))
            button = view.children[0]
            await button.callback(interaction)
            self.assertEqual(pressed, [(7, "T1:loop:yes")])
            self.assertEqual(edits, [[True, True]])

        run(go())

    def test_from_custom_id_keeps_the_style(self):
        import discord

        from barter.platforms.discord_bot import build_bot

        _, _, _, ActionButton = build_bot(ExchangeService())
        base = discord.ui.Button(label="Got it ✓", custom_id="T3:receipt:yes", style=discord.ButtonStyle.success)
        match = ActionButton.__discord_ui_compiled_template__.fullmatch(base.custom_id)
        item = run(ActionButton.from_custom_id(None, base, match))
        self.assertEqual((item.custom_id, item.item.label, item.item.style),
                         ("T3:receipt:yes", "Got it ✓", discord.ButtonStyle.success))


if __name__ == "__main__":
    unittest.main()
