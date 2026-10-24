"""The web server, through FastAPI's TestClient. Offline: bots answer inline, nothing is fetched.

Skipped when the `web` extra (FastAPI) isn't installed, so `python3 -m unittest` still runs the rest.
"""

import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HAVE_WEB = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None

if HAVE_WEB:
    from fastapi.testclient import TestClient

    from barter.server import create_app, stream
    from barter.service import ExchangeService


@unittest.skipUnless(HAVE_WEB, "needs the web extra: uv pip install -e '.[web]'")
class Api(unittest.TestCase):
    def setUp(self):
        self.dist = tempfile.TemporaryDirectory()
        Path(self.dist.name, "index.html").write_text("<!doctype html><title>Barter Bros</title>")
        self.service = ExchangeService()
        self.client = TestClient(create_app(self.service, Path(self.dist.name)))

    def tearDown(self):
        self.dist.cleanup()

    def post(self, url, body=None, status=200):
        r = self.client.post(url, json=body)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_demo_end_to_end(self):
        seeded = self.post("/api/demo/seed?scenario=book-charger-case-3way")
        sid = seeded["session"]
        view = self.post(f"/api/sessions/{sid}/messages", {"text": seeded["suggested"]})
        trade = view["trades"][0]
        self.assertEqual((trade["state"], len(trade["members"])), ("proposed", 3))
        self.assertEqual(trade["mine"]["worth"], {"value": 35, "floor": 30.0})
        tid = trade["id"]
        view = self.post(f"/api/sessions/{sid}/trades/{tid}/vote", {"yes": True})
        self.assertEqual(view["trades"][0]["plan"], {"slot": "Sat 11:00 AM-11:30 AM", "spot": "Noble Library"})
        view = self.post(f"/api/sessions/{sid}/trades/{tid}/meetup", {"yes": True})
        self.assertEqual(view["trades"][0]["state"], "meetup_agreed")
        view = self.post(f"/api/sessions/{sid}/trades/{tid}/receipt", {"got_it": True})
        self.assertEqual(view["trades"][0]["state"], "handed_off")
        self.assertEqual(self.client.get(f"/api/sessions/{sid}").json()["trades"][0]["state"], "handed_off")
        kinds = [m["kind"] for m in self.client.get("/api/wire").json()]
        self.assertEqual(kinds[-1], "done")
        self.assertEqual(self.client.get("/api/exchange").json()["stats"], {"agents": 4, "listings": 1, "loops": 1})

    def test_market_and_reset(self):
        self.post("/api/demo/seed?scenario=book-charger-case-3way")
        self.assertEqual(self.post("/api/demo/seed?scenario=market"), {"bots": 12})
        self.assertEqual(self.client.get("/api/exchange").json()["stats"]["loops"], 3)
        wire = self.client.get("/api/wire?after=5").json()
        self.assertEqual(wire[0]["i"], 5)
        self.post("/api/demo/reset")
        self.assertEqual(self.client.get("/api/exchange").json()["stats"], {"agents": 0, "listings": 0, "loops": 0})

    def test_fresh_session(self):
        made = self.post("/api/sessions", {"channel": "imessage"})
        view = self.client.get(f"/api/sessions/{made['session']}").json()
        self.assertEqual((view["handle"], view["channel"]), (made["handle"], "imessage"))
        self.assertNotIn("Guest", json.dumps(view))
        self.post("/api/sessions", {"channel": "fax"}, status=400)

    def test_errors(self):
        self.assertEqual(self.client.get("/api/sessions/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/nothing-here").status_code, 404)
        self.assertEqual(self.client.get("/api/events?session=nope").status_code, 404)
        self.post("/api/demo/seed?scenario=nope", status=404)
        seeded = self.post("/api/demo/seed")
        sid = seeded["session"]
        self.post(f"/api/sessions/{sid}/messages", {"text": "  "}, status=400)
        tid = self.post(f"/api/sessions/{sid}/messages", {"text": seeded["suggested"]})["trades"][0]["id"]
        self.post(f"/api/sessions/{sid}/trades/{tid}/receipt", {"got_it": True}, status=409)
        self.post(f"/api/sessions/{sid}/trades/T9/vote", {"yes": True}, status=404)

    def test_serves_the_web_app(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Barter Bros", r.text)

    def test_unbuilt_web_app_says_how_to_build_it(self):
        with tempfile.TemporaryDirectory() as empty:
            r = TestClient(create_app(ExchangeService(), Path(empty))).get("/")
        self.assertIn("npm run build", r.text)


@unittest.skipUnless(HAVE_WEB, "needs the web extra")
class Events(unittest.TestCase):
    def take(self, gen, n):
        async def run():
            return [await anext(gen) for _ in range(n)]

        return asyncio.run(run())

    def parse(self, chunk):
        lines = dict(line.split(": ", 1) for line in chunk.strip().split("\n"))
        return lines["event"], json.loads(lines["data"])

    def test_global_stream_is_the_wire_then_the_exchange(self):
        service = ExchangeService()
        seeded = service.seed_scenario("book-charger-case-3way")
        n = len(service.exchange.log)
        chunks = self.take(stream(service, None, 0, poll=0), n + 2)
        self.assertEqual(chunks[0], ": connected\n\n")
        events = [self.parse(c) for c in chunks[1:]]
        self.assertEqual([e for e, _ in events], ["wire"] * n + ["exchange"])
        self.assertEqual(events[0][1]["kind"], "offer")
        self.assertNotIn(seeded["session"], json.dumps(events))  # a session id is a secret too

    def test_since_skips_the_backlog(self):
        service = ExchangeService()
        service.seed_scenario("book-charger-case-3way")
        n = len(service.exchange.log)
        chunks = self.take(stream(service, None, n - 1, poll=0), 3)
        self.assertEqual([self.parse(c)[0] for c in chunks[1:]], ["wire", "exchange"])

    def test_session_stream_carries_your_private_view(self):
        service = ExchangeService()
        seeded = service.seed_scenario("book-charger-case-3way")
        service.post_message(seeded["session"], seeded["suggested"])
        chunks = self.take(stream(service, seeded["session"], 0, poll=0), 2)
        event, view = self.parse(chunks[1])
        self.assertEqual(event, "session")
        self.assertEqual(view["trades"][0]["mine"]["worth"]["floor"], 30.0)


if __name__ == "__main__":
    unittest.main()
