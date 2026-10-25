"""The Discord bot: a student DMs their agent, and the agent DMs back offers with buttons.

    .venv/bin/python -m barter.platforms.discord_bot --guild <server id> [--web-port 8787] [--market] [--claude]
    .venv/bin/python -m barter.platforms.discord_bot --dry-run    # print the config, connect to nothing

A DM is the conversation: it goes to `ExchangeService.handle_message("discord", <user id>, text)`.
Every agent reply, whether it answers that DM or arrives later (when the other agents answer), comes
back through `ExchangeService.subscribe`, and this bot posts it as one Discord message: the text,
one button per action (custom_id = the action id, e.g. "T1:loop:yes"), and one embed per partner
on the reply that reveals who they are. Replies take that single path, in order, because the
service pushes every reply to its listeners, including the ones it also returns; posting both
would send each one twice. A button press calls `press`, disables that message's buttons, and the
replies it causes arrive the same way.

Buttons are persistent: a `DynamicItem` whose template matches every action id is registered at
startup, so a button on an old message still reaches the service after a restart. The service
keeps its state in memory, though, so after a restart that press is answered "DM me first".

`--web-port` serves the web app from `barter/server.py` in the same event loop, over the same
service, so the Exchange page shows the live Discord traffic. `--market` seeds the twelve demo bots.

Everything above `build_bot()` is plain Python over duck-typed Discord objects, tested with fakes;
discord.py is imported only in `build_bot()` and `main()`. The token is read from the macOS
Keychain into memory (service `discord-bot-token`, account `hercules`) and never printed, logged,
or passed on a command line or in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from collections.abc import Awaitable, Callable

from ..profiles import PUBLIC_KEYS
from ..service import HELP, ExchangeService, ServiceError

CHANNEL = "discord"
ACCOUNT, SERVICE = "hercules", "discord-bot-token"
ADD_TOKEN = f"security add-generic-password -a {ACCOUNT} -s {SERVICE} -w"
ACTION_TEMPLATE = r"(?:T\d+:(?:loop|receipt)|T\d+:meetup:\d+|L\d+:giveaway):(?:yes|no)"  # every id the service puts on a button
GUILD_HINT = "DM me what you have and what you want"
STALE = "I don't know you yet (the exchange restarted since that message). DM me what you have and what you want."
LIVE_STATES = ("proposed", "accepted_by_all", "meetup_agreed")
WAITING = {"proposed": "everyone to accept", "accepted_by_all": "everyone to say yes to the meetup",
           "meetup_agreed": "the handoff (tap \"Got it\" when you have your item)"}
# The web app's avatar inks (web/src/index.css), as Discord embed colours.
COLORS = {"blue": 0x2563FF, "coral": 0xFF6B57, "lime": 0xA3E635, "purple": 0x9B5CFF, "yellow": 0xFFDC58}


# ------------------------------------------------------------------ rendering ---
# Plain dicts: {"content", "buttons": [{"custom_id", "label", "style"}], "embeds": [Discord embed dicts]}.
# `build_bot` turns them into discord.py objects.

def profile_embed(p: dict) -> dict:
    """A partner's public profile as a Discord embed. Reads only `PUBLIC_KEYS`."""
    p = {k: p[k] for k in PUBLIC_KEYS}
    record = f"{p['showed_up']} showed up · {p['no_shows']} no-show{'' if p['no_shows'] == 1 else 's'}"
    return {
        "title": p["name"],
        "description": p["bio"] or "No bio yet.",
        "color": COLORS.get(p["color"], COLORS["yellow"]),
        "author": {"name": f"{p['initials']} · {p['handle']}"},
        "fields": [
            {"name": "Campus", "value": p["campus"] or "—", "inline": True},
            {"name": "Trades done", "value": str(p["completed"]), "inline": True},
            {"name": "Show-up record", "value": record, "inline": True},
            {"name": "Member since", "value": p["member_since"], "inline": True},
        ],
        "footer": {"text": "Public profile · contact details stay with the agents"},
    }


def render(reply: dict) -> dict:
    """One service reply as one Discord message."""
    return {
        "content": reply["text"],
        "buttons": [{"custom_id": a["id"], "label": a["label"], "style": a["style"]} for a in reply["actions"]],
        "embeds": [profile_embed(p) for p in reply.get("profiles", [])],
    }


def is_discord_user(channel: str, user_ref: str | None) -> bool:
    """A real Discord user id. Web sessions, bots and demo refs ("demo-jordan") are not."""
    return channel == CHANNEL and user_ref is not None and user_ref.isdigit()


# ------------------------------------------------------------------ the adapter ---

class Adapter:
    """What the bot does, over duck-typed Discord objects.

    `fetch_user(int)` returns something with an async `send(**kwargs)`, and `convert(rendered)`
    turns a rendered dict into those kwargs; `build_bot` passes discord.py's, the tests pass fakes.
    """

    def __init__(self, service: ExchangeService, fetch_user: Callable[[int], Awaitable] | None = None,
                 convert: Callable[[dict], dict] = lambda m: m):
        self.service, self.fetch_user, self.convert = service, fetch_user, convert
        self.channels: dict[str, object] = {}  # user id -> their DM channel, once they've written
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue | None = None
        self.task: asyncio.Task | None = None
        self.queued = self.handled = 0  # replies handed to the loop, and replies it has sent (or failed to)
        service.subscribe(self.listener)

    def start(self) -> None:
        """Start delivering replies. Call on the bot's event loop."""
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.task = self.loop.create_task(self.deliver())

    def listener(self, channel: str, user_ref: str | None, reply: dict) -> None:
        """Runs under the service lock, on whichever thread said it (a bot's timer, a worker thread).
        Only hands the reply to the event loop."""
        if self.loop is None or not is_discord_user(channel, user_ref):
            return
        self.queued += 1  # every listener call holds the service lock, so this never races itself
        self.loop.call_soon_threadsafe(self.queue.put_nowait, (user_ref, render(reply)))

    async def deliver(self) -> None:
        while True:
            user_ref, message = await self.queue.get()
            try:
                await self.send(user_ref, message)
            except Exception as e:  # one failed DM (blocked, deleted account) must not stop the rest
                print(f"couldn't DM user {user_ref}: {type(e).__name__}", file=sys.stderr)
            finally:
                self.handled += 1

    async def flush(self) -> None:
        """Wait until every reply queued so far has been sent. A hand-off from another thread can
        reach the loop after the call that caused it returns, so this counts, rather than asking the
        queue whether it is empty."""
        while self.handled < self.queued:
            await asyncio.sleep(0.001)

    async def send(self, user_ref: str, message: dict) -> None:
        target = self.channels.get(user_ref)
        if target is None:
            target = await self.fetch_user(int(user_ref))
        await target.send(**self.convert(message))

    async def on_message(self, message, me) -> None:
        """A DM is a message to your agent. In a server, only an @mention gets an answer: a hint."""
        author = message.author
        if getattr(author, "bot", False):
            return
        if message.guild is not None:
            if me is not None and any(u.id == me.id for u in message.mentions or []):
                await message.channel.send(GUILD_HINT)
            return
        user_ref = str(author.id)
        self.channels[user_ref] = message.channel
        try:  # the replies come back through `listener`
            await asyncio.to_thread(self.service.handle_message, CHANNEL, user_ref, message.content or "",
                                    author.display_name)
        except ServiceError as e:
            await message.channel.send(str(e))

    async def on_press(self, user_id, action_id: str, disable: Callable[[], Awaitable],
                       notify: Callable[[str], Awaitable]) -> None:
        """A button: disable that message's buttons (which answers the interaction at once), then tell
        the service. `notify` sends the presser a private note when the service refuses: an unknown user
        or trade means the exchange restarted, and a trade they're not in gets the service's own words."""
        await disable()
        try:
            await asyncio.to_thread(self.service.press, CHANNEL, str(user_id), action_id)
        except ServiceError as e:
            await notify(STALE if e.status == 404 else str(e))

    # ---------------------------------------------------------- slash commands

    def help(self) -> str:
        return HELP

    def profile(self, user_id, name: str, campus: str | None = None, bio: str | None = None) -> tuple[str, dict]:
        """Set your campus and bio. Returns a private note and your profile as an embed."""
        with self.service.lock:
            s = self.service.user_session(CHANNEL, str(user_id), name)
            self.service.update_profile(s.id, campus=campus, bio=bio)
            view = self.service.profile_view(s.id)
        changed = [k for k, v in (("campus", campus), ("bio", bio)) if v is not None]
        note = f"Updated your {' and '.join(changed)}." if changed else "Your profile:"
        return (note + " Trade partners see this only after everyone in a trade says yes.", profile_embed(view))

    def status(self, user_id) -> str:
        """Your open trade, or your listing if you're not in one."""
        with self.service.lock:
            s = self.service.by_user.get((CHANNEL, str(user_id)))
            if s is None:
                return "You haven't told me anything yet. " + GUILD_HINT + "."
            view = self.service.session_view(s.id)
        live = next((t for t in view["trades"] if t["state"] in LIVE_STATES), None)
        if live is None:
            listing = view["listing"]
            if listing["have"] is None and not listing["wants"]:
                return "No listing and no open trade. " + GUILD_HINT + "."
            have = listing["have"]["label"] if listing["have"] else "nothing to give"
            wants = "; ".join(w["label"] for w in listing["wants"]) or "nothing back"
            where = "listed, waiting for a match" if listing["open"] else "not listed"
            return f"No open trade. Your listing ({where}): you have {have}; you want {wants}."
        mine = live["mine"]
        shape = "chain" if live["shape"] == "chain" else f"{len(live['members'])}-way loop"
        lines = [f"**{live['id']}**, a {shape}: waiting for {WAITING[live['state']]}."]
        lines.append(f"You give: {mine['gives']['label']} to {mine['to']}." if mine["gives"] else "You give nothing.")
        lines.append(f"You get: {mine['gets']['label']} from {mine['from']}." if mine["gets"] else "You get nothing back.")
        if live["plan"]:
            lines.append(f"Meetup: {live['plan']['slot']} at {live['plan']['spot']}.")
        return "\n".join(lines)


# ------------------------------------------------------------------ discord.py wiring ---

def build_bot(service: ExchangeService, guild_id: int | None = None):
    """The discord.py client with DMs, persistent buttons and the /barter commands. Does not connect.

    Returns (client, tree, adapter, ActionButton)."""
    import discord
    from discord import app_commands

    styles = {"primary": discord.ButtonStyle.primary, "secondary": discord.ButtonStyle.secondary,
              "success": discord.ButtonStyle.success}

    intents = discord.Intents.default()  # DMs and @mentions carry their text without the Message Content intent
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    class ActionButton(discord.ui.DynamicItem[discord.ui.Button], template=ACTION_TEMPLATE):
        """Any button the service puts on a reply, before or after a restart."""

        def __init__(self, action_id: str, label: str = "", style: str = "secondary"):
            super().__init__(discord.ui.Button(label=label, custom_id=action_id,
                                               style=styles.get(style, discord.ButtonStyle.secondary)))

        @classmethod
        async def from_custom_id(cls, interaction, item, match):
            button = cls(match.group(0), item.label or "")
            button.item.style = item.style  # keep the pressed message's look
            return button

        async def callback(self, interaction: discord.Interaction):
            async def disable():
                view = self.view  # discord.py rebuilt it from the pressed message
                for child in view.children:
                    child.disabled = True
                await interaction.response.edit_message(view=view)

            async def notify(text: str):
                await interaction.followup.send(text, ephemeral=True)

            await adapter.on_press(interaction.user.id, self.custom_id, disable, notify)

    def convert(m: dict) -> dict:
        kwargs = {"content": m["content"]}
        if m["buttons"]:
            view = discord.ui.View(timeout=None)
            for b in m["buttons"]:
                view.add_item(ActionButton(b["custom_id"], b["label"], b["style"]))
            kwargs["view"] = view
        if m["embeds"]:
            kwargs["embeds"] = [discord.Embed.from_dict(e) for e in m["embeds"]]
        return kwargs

    adapter = Adapter(service, client.fetch_user, convert)

    group = app_commands.Group(name="barter", description="Barter Bros: your trading agent")

    @group.command(name="help", description="How Barter Bros works")
    async def barter_help(interaction: discord.Interaction):
        await interaction.response.send_message(adapter.help(), ephemeral=True)

    @group.command(name="profile", description="Set the campus and bio trade partners see once everyone accepts")
    @app_commands.describe(campus="e.g. ASU Tempe", bio="a line about you (160 characters at most)")
    async def barter_profile(interaction: discord.Interaction, campus: str | None = None, bio: str | None = None):
        try:
            note, embed = await asyncio.to_thread(adapter.profile, interaction.user.id,
                                                  interaction.user.display_name, campus, bio)
        except ServiceError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message(note, embed=discord.Embed.from_dict(embed), ephemeral=True)

    @group.command(name="status", description="Your open trade")
    async def barter_status(interaction: discord.Interaction):
        text = await asyncio.to_thread(adapter.status, interaction.user.id)
        await interaction.response.send_message(text, ephemeral=True)

    tree.add_command(group)

    async def setup_hook():  # once per start, not on every reconnect like on_ready
        client.add_dynamic_items(ActionButton)
        adapter.start()
        if guild_id:  # a test server: the commands appear at once
            guild = discord.Object(id=guild_id)
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        else:  # everywhere: Discord can take up to an hour to show them
            await tree.sync()

    client.setup_hook = setup_hook

    @client.event
    async def on_ready():
        print(f"logged in as {client.user}; DM it, or use /barter", file=sys.stderr)

    @client.event
    async def on_message(message: discord.Message):
        await adapter.on_message(message, client.user)

    return client, tree, adapter, ActionButton


def keychain_token(run=subprocess.run) -> str:
    """The bot token, read from the Keychain into memory. Never printed, logged or written."""
    proc = run(["security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"],
               capture_output=True, text=True)
    token = proc.stdout.strip() if proc.returncode == 0 else ""
    if not token:
        raise SystemExit(f"No Discord bot token in the Keychain (service '{SERVICE}', account '{ACCOUNT}').\n"
                         f"Add it with:  {ADD_TOKEN}\n(it prompts for the token, so it never lands in your "
                         "shell history)")
    return token


async def serve(service: ExchangeService, token: str, guild_id: int | None, web_port: int | None,
                host: str = "127.0.0.1") -> None:
    """The bot, and the web app on the same service when `web_port` is set, in one event loop."""
    client, _, _, _ = build_bot(service, guild_id)
    server = web = None
    if web_port:
        import uvicorn

        from ..server import create_app
        server = uvicorn.Server(uvicorn.Config(create_app(service), host=host, port=web_port, log_level="warning"))
        web = asyncio.create_task(server.serve())
        print(f"web app on http://{host}:{web_port}", file=sys.stderr)
    async with client:
        bot = asyncio.create_task(client.start(token))
        tasks = {bot} | ({web} if web else set())
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:  # either one stopping (Ctrl-C reaches uvicorn first) stops both
            if server is not None:
                server.should_exit = True
            await client.close()
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in done:
            task.result()  # a login failure, say, surfaces here


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m barter.platforms.discord_bot",
                                 description="Run the Barter Bros Discord bot (and, with --web-port, the web app).")
    ap.add_argument("--guild", type=int, help="server id to register /barter in at once (else global, up to an hour)")
    ap.add_argument("--web-port", type=int, help="also serve the web app on this port, over the same exchange")
    ap.add_argument("--host", default="127.0.0.1", help="the web app's host (default 127.0.0.1)")
    ap.add_argument("--market", action="store_true", help="seed the twelve demo bots")
    ap.add_argument("--claude", action="store_true", help="parse DMs with Claude when a key is configured")
    ap.add_argument("--dry-run", action="store_true", help="print the config and exit without connecting")
    args = ap.parse_args(argv)
    if args.dry_run:
        print("Barter Bros Discord bot (dry run: nothing read from the Keychain, nothing connected)")
        print(f"  guild:     {args.guild or 'none (global commands, up to an hour to appear)'}")
        print(f"  web app:   {f'http://{args.host}:{args.web_port}' if args.web_port else 'off'}")
        print(f"  market:    {'12 demo bots' if args.market else 'off'}")
        print(f"  parser:    {'Claude, if a key is configured' if args.claude else 'rules (offline)'}")
        print(f"  token:     Keychain service '{SERVICE}', account '{ACCOUNT}'")
        return 0
    try:
        import discord  # noqa: F401
    except ImportError:
        print("discord.py is not installed: uv pip install --python .venv/bin/python -e '.[web,discord]'",
              file=sys.stderr)
        return 1
    token = keychain_token()
    from ..server import make_service
    service = make_service(args.claude)
    if args.market:
        service.seed_market()
    try:
        asyncio.run(serve(service, token, args.guild, args.web_port, args.host))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
