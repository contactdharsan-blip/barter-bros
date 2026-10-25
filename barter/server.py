"""The Barter Bros web server: REST and Server-Sent Events over `barter.service`, plus the web app.

    .venv/bin/python -m barter.server --port 8787            # rule parser, offline
    .venv/bin/python -m barter.server --port 8787 --claude   # parse with Claude if a key is configured

Needs the `web` extra (FastAPI, uvicorn). The built frontend is served from `web/dist`
(`cd web && npm run build`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import parse as parser
from .service import ExchangeService, ServiceError

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
BOT_DELAY = 0.9  # seconds between bot answers, so the ticks arrive one by one
POLL = 0.2  # seconds between SSE checks


class NewSession(BaseModel):
    name: str = "Guest"
    channel: str = "web"
    campus: str | None = None
    bio: str | None = None


class ProfileEdit(BaseModel):
    name: str | None = None
    campus: str | None = None
    bio: str | None = None


class ChatIn(BaseModel):
    text: str
    name: str | None = None


class Press(BaseModel):
    action: str


class YesNo(BaseModel):
    yes: bool


class Receipt(BaseModel):
    got_it: bool = True


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream(service: ExchangeService, session: str | None, since: int, request: Request | None = None,
                 poll: float = POLL):
    """Server-Sent Events: the session's view when it changes, or the wire and the exchange view."""
    cursor = {"wire": since}
    yield ": connected\n\n"
    while request is None or not await request.is_disconnected():
        events, cursor = service.updates(cursor, session)
        for event, data in events:
            yield sse(event, data)
            if event == "gone":
                return
        await asyncio.sleep(poll)


def create_app(service: ExchangeService, dist: Path = DIST) -> FastAPI:
    app = FastAPI(title="Barter Bros")

    def call(fn, *args):
        try:
            return fn(*args)
        except ServiceError as e:
            raise HTTPException(e.status, str(e)) from e

    @app.get("/api/health")
    def health():
        return {"ok": True, "parser": "claude" if service.client is not None else "rules"}

    # A chat app's adapter (Discord bot, iMessage bridge) can run in its own process and call these.
    # Students type to their agent there, never into the website. Bound to 127.0.0.1 by default.
    @app.post("/api/channels/{channel}/{user_ref}/messages")
    def channel_message(channel: str, user_ref: str, body: ChatIn):
        return {"replies": call(service.handle_message, channel, user_ref, body.text, body.name)}

    @app.post("/api/channels/{channel}/{user_ref}/press")
    def channel_press(channel: str, user_ref: str, body: Press):
        return {"replies": call(service.press, channel, user_ref, body.action)}

    # The web companion, by session id.
    @app.post("/api/sessions")
    def new_session(body: NewSession):
        def make():
            s = service.create_session(body.name.strip() or "Guest", body.channel)
            service.update_profile(s.id, campus=body.campus, bio=body.bio)
            return s

        s = call(make)
        return {"session": s.id, "handle": s.handle}

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str):
        return call(service.session_view, sid)

    @app.get("/api/sessions/{sid}/profile")
    def my_profile(sid: str):
        return call(service.profile_view, sid)

    @app.patch("/api/sessions/{sid}/profile")
    def edit_profile(sid: str, body: ProfileEdit):
        call(service.update_profile, sid, body.name, body.campus, body.bio)
        return call(service.profile_view, sid)

    @app.get("/api/sessions/{sid}/profiles/{handle}")
    def partner_profile(sid: str, handle: str):
        return call(service.profile_view, sid, handle)

    @app.post("/api/sessions/{sid}/trades/{tid}/vote")
    def vote(sid: str, tid: str, body: YesNo):
        call(service.vote, sid, tid, body.yes)
        return call(service.session_view, sid)

    @app.post("/api/sessions/{sid}/trades/{tid}/meetup")
    def meetup(sid: str, tid: str, body: YesNo):
        call(service.answer_meetup, sid, tid, body.yes)
        return call(service.session_view, sid)

    @app.post("/api/sessions/{sid}/trades/{tid}/receipt")
    def receipt(sid: str, tid: str, body: Receipt):
        call(service.receipt, sid, tid, body.got_it)
        return call(service.session_view, sid)

    @app.get("/api/exchange")
    def exchange():
        return service.exchange_view()

    @app.get("/api/wire")
    def wire(after: int = 0):
        return service.wire_view(max(after, 0))

    @app.post("/api/demo/seed")
    def seed(scenario: str = "book-charger-case-3way", human: str | None = None):
        if scenario == "market":
            return service.seed_market()
        return call(service.seed_scenario, scenario, human)

    @app.post("/api/demo/reset")
    def reset():
        service.reset()
        return {"ok": True}

    @app.get("/api/events")
    def events(request: Request, session: str | None = None, since: int = 0):
        if session is not None:
            call(service.session, session)
        return StreamingResponse(stream(service, session, max(since, 0), request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PATCH"])
    def no_such_api(rest: str):
        raise HTTPException(404, "no such endpoint")

    if (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    else:
        @app.get("/")
        def not_built():
            return HTMLResponse("<p>The web app isn't built. Run <code>cd web && npm install && npm run build</code>.</p>")

    return app


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    ap = argparse.ArgumentParser(prog="python -m barter.server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--claude", action="store_true", help="parse with Claude when a key is configured")
    args = ap.parse_args(argv)
    client = parser.make_client() if args.claude else None  # only with --claude does anything touch the Keychain
    service = ExchangeService(backend="claude" if client is not None else "rules", client=client, delay=BOT_DELAY)
    if args.claude and client is None:
        print("--claude: no SDK or no key configured, so the rule parser is used")
    uvicorn.run(create_app(service), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
