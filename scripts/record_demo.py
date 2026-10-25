"""Record the web demo as a replay, for the static site (GitHub Pages), where no Python runs.

    .venv/bin/python scripts/record_demo.py                 # writes web/public/demo-replay.json
    .venv/bin/python scripts/record_demo.py --out some.json

Drives the real `barter.service` in-process (no network, no Claude, no Discord) through the demo
the web page runs: Jordan's seeded DM, the busy market, a DM with Jordan's free times and the
"That's right" that confirms them, Accept, "Works for me" on the meetup, and "Got it". After each
step it stores what the web app would have read from the server at that point: Jordan's
SessionView, the ExchangeView, and the wire messages that step sent.

The output is deterministic, so the committed file only changes when the service does: bots answer
inline (delay=None), the clock moves 25 s per step from a fixed start, and the random handles and
session id come from a seeded generator instead of `secrets` (only in this script's process).
Everyone in it is invented.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from barter import agent as agent_module  # noqa: E402
from barter import service as service_module  # noqa: E402
from barter.service import ExchangeService  # noqa: E402

OUT = ROOT / "web" / "public" / "demo-replay.json"
SCENARIO = "book-charger-case-3way"
CHANNEL, USER = "discord", "demo-jordan"
TIMES_DM = "I'm free saturday morning"
START = datetime(2026, 10, 24, 17, 0, tzinfo=timezone.utc).timestamp()  # 10:00 AM in Tempe
STEP_SECONDS = 25


class SeededSecrets:
    """Stands in for `secrets` in the service and agent modules, so handles repeat run to run."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    def token_hex(self, n: int) -> str:
        return self.rng.randbytes(n).hex()

    def token_urlsafe(self, n: int) -> str:
        return self.rng.randbytes(n).hex()


def record() -> dict:
    real = service_module.secrets, agent_module.secrets
    service_module.secrets = agent_module.secrets = SeededSecrets(181)
    try:
        return _record()
    finally:
        service_module.secrets, agent_module.secrets = real


def _record() -> dict:
    now = [START]
    service = ExchangeService(delay=None, clock=lambda: now[0])

    steps: list[dict] = []
    sid = ""

    def capture(action: dict) -> None:
        sent = sum(len(s["wire"]) for s in steps)
        steps.append({"action": action, "session": service.session_view(sid),
                      "exchange": service.exchange_view(), "wire": service.wire_view(sent)})
        now[0] += STEP_SECONDS

    def open_action(label: str) -> dict:
        """The open button with this label in Jordan's thread, as the demo driver would show it."""
        for line in reversed(service.session_view(sid)["chat"]):
            for a in line["actions"]:
                if a["open"] and a["label"] == label:
                    return a
        raise SystemExit(f"record_demo: no open {label!r} button; the demo flow changed")

    def press(label: str) -> None:
        a = open_action(label)
        service.press(CHANNEL, USER, a["id"])
        capture({"kind": "press", "id": a["id"], "label": a["label"]})

    seeded = service.seed_scenario(SCENARIO)
    sid = seeded["session"]
    capture({"kind": "seed", "label": "Try the demo (be Jordan)"})
    service.seed_market()
    capture({"kind": "market", "label": "Busy market"})
    service.handle_message(CHANNEL, USER, TIMES_DM)
    capture({"kind": "message", "label": f"Jordan DMs: “{TIMES_DM}”", "text": TIMES_DM})
    for label in ("That's right", "Accept", "Works for me", "Got it ✓"):
        press(label)

    t1 = next(t for t in steps[-1]["exchange"]["trades"] if t["id"] == "T1")
    if t1["state"] != "handed_off":
        raise SystemExit(f"record_demo: T1 ended {t1['state']}, expected handed_off")
    return {
        "about": "A recording of the Barter Bros web demo: the real service, run in-process by "
                 "scripts/record_demo.py. Bots are scripted and everyone is invented.",
        "scenario": SCENARIO,
        "channel": CHANNEL,
        "user_ref": USER,
        "handle": seeded["handle"],
        "steps": steps,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/record_demo.py")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    data = record()
    args.out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"{args.out}: {len(data['steps'])} steps, "
          f"{sum(len(s['wire']) for s in data['steps'])} wire messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
