"""The rule parser on real-shaped phrasings, and the Claude backend with a mocked client (no network)."""

import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

from barter import keychain, parse
from barter.parse import LISTING_SCHEMA, PARSER_SYSTEM_PROMPT, parse_claude, parse_rules

# text -> (have category, have attributes, have condition, [(want category, want attributes)], floor)
CASES = [
    ("I have a BIO 181 textbook, need a USB-C charger",
     ("textbook", {"course": "BIO 181"}, "good"), [("charger", {"connector": "usb-c"})], None),
    ("I have a BIO 181 textbook I'll never open again. I need a USB-C charger, fast charging if possible. "
     "I'd give the textbook for anything worth at least $30 to me.",
     ("textbook", {"course": "BIO 181"}, "good"), [("charger", {"connector": "usb-c"})], 30.0),
    ("Got an iPhone 15 case, like new. Need a BIO 181 textbook.",
     ("phone_case", {"model": "iPhone 15"}, "new"), [("textbook", {"course": "BIO 181"})], None),
    ("Have a 20W USB-C charger, good condition. Looking for an iPhone 15 case.",
     ("charger", {"connector": "usb-c", "watts": 20}, "good"), [("phone_case", {"model": "iPhone 15"})], None),
    ("need a type-c charger", None, [("charger", {"connector": "usb-c"})], None),
    ("looking for a usbc cable", None, [("charger", {"connector": "usb-c"})], None),
    ("Have a lightning charger, need a desk lamp.",
     ("charger", {"connector": "lightning"}, "good"), [("furniture", {"kind": "desk lamp"})], None),
    ("got an old micro usb charger, want a hoodie",
     ("charger", {"connector": "micro-usb"}, "fair"), [("clothing", {"kind": "hoodie"})], None),
    ("need a charger for my iphone 12", None, [("charger", {"connector": "lightning"})], None),
    ("need a charger, usb-c, 30w", None, [("charger", {"connector": "usb-c", "watts": 30})], None),
    ("I have an iPhone 14 Pro case, need a TI-84",
     ("phone_case", {"model": "iPhone 14 Pro"}, "good"), [("calculator", {"model": "TI-84"})], None),
    ("Have a Pixel 8 case, want a ti 84 plus ce",
     ("phone_case", {"model": "Pixel 8"}, "good"), [("calculator", {"model": "TI-84"})], None),
    ("Trading my TI-84 Plus CE for a lightning charger or a micro usb cable",
     ("calculator", {"model": "TI-84"}, "good"),
     [("charger", {"connector": "lightning"}), ("charger", {"connector": "micro-usb"})], None),
    ("Have a TI-84, need a desk. I'd want at least $41 for the calculator.",
     ("calculator", {"model": "TI-84"}, "good"), [("furniture", {"kind": "desk"})], 41.0),
    ("selling a TI-Nspire, looking for a mini fridge",
     ("calculator", {"model": "TI-Nspire"}, "good"), [("furniture", {"kind": "mini fridge"})], None),
    ("Moving out Sunday, giving away a desk for free.", ("furniture", {"kind": "desk"}, "good"), [], None),
    ("giving away a futon, beat up but works", ("furniture", {"kind": "futon"}, "fair"), [], None),
    ("Looking for a hoodie, size M. I don't have anything to trade.",
     None, [("clothing", {"kind": "hoodie", "size": "M"})], None),
    ("I have a jacket, size S. Need a chair.",
     ("clothing", {"kind": "jacket", "size": "S"}, "good"), [("furniture", {"kind": "chair"})], None),
    ("have a brand new XL t-shirt, want a chem 113 textbook",
     ("clothing", {"kind": "t-shirt", "size": "XL"}, "new"), [("textbook", {"course": "CHM 113"})], None),
    ("I have a BIO 181 textbook, 3rd edition. Looking for a TI-84.",
     ("textbook", {"course": "BIO 181", "edition": "3"}, "good"), [("calculator", {"model": "TI-84"})], None),
    ("need the MAT 265 book", None, [("textbook", {"course": "MAT 265"})], None),
    ("Have a Galaxy S23 case. Need a 65w usb-c brick, at least $10 for the case",
     ("phone_case", {"model": "Galaxy S23"}, "good"), [("charger", {"connector": "usb-c", "watts": 65})], 10.0),
    ("got a desk chair, need a lightning or usb-c charger",
     ("furniture", {"kind": "desk chair"}, "good"),
     [("charger", {"connector": "lightning"}), ("charger", {"connector": "usb-c"})], None),
]


class Rules(unittest.TestCase):
    def test_phrasings(self):
        self.assertGreaterEqual(len(CASES), 20)
        for text, have, wants, floor in CASES:
            with self.subTest(text=text):
                listing = parse_rules(text)
                self.assertEqual(listing.parser, "rules")
                if have is None:
                    self.assertIsNone(listing.have)
                else:
                    self.assertEqual((listing.have.category, listing.have.attributes, listing.have.condition), have)
                self.assertEqual([(w.category, w.attributes) for w in listing.wants], wants)
                self.assertEqual(listing.floor, floor)

    def test_a_soft_preference_adds_no_want(self):
        listing = parse_rules("need a usb-c charger, fast charging if possible")
        self.assertEqual(len(listing.wants), 1)

    def test_default_backend_is_rules_and_never_looks_for_a_key(self):
        with mock.patch.object(keychain, "read_key") as read_key:
            self.assertEqual(parse.parse("need a ti-84").parser, "rules")
        read_key.assert_not_called()


def fake_client(stop_reason="end_turn", text=None, error=None):
    response = SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text or "")],
    )
    create = mock.Mock(side_effect=error) if error else mock.Mock(return_value=response)
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))), create


GOOD = {
    "have": {"category": "textbook", "condition": "good",
             "attributes": {"connector": None, "watts": None, "model": None, "course": "BIO 181", "title": None,
                            "edition": None, "kind": None, "size": None}},
    "wants": [{"category": "charger", "min_condition": None,
               "attributes": {"connector": "usb-c", "watts": 20, "model": None, "course": None, "title": None,
                              "edition": None, "kind": None, "size": None}}],
    "floor": 30,
}
TEXT = "textbook for bio, want a fast usb c charger, 30 bucks minimum"


class Claude(unittest.TestCase):
    def test_good_json_becomes_a_listing(self):
        client, create = fake_client(text=json.dumps(GOOD))
        listing = parse_claude(TEXT, client)
        self.assertEqual(listing.parser, "claude")
        self.assertEqual(listing.have.attributes, {"course": "BIO 181"})
        self.assertEqual(listing.wants[0].attributes, {"connector": "usb-c", "watts": 20})
        self.assertEqual(listing.floor, 30.0)
        create.assert_called_once()

    def test_call_shape(self):
        client, create = fake_client(text=json.dumps(GOOD))
        parse.parse(TEXT, backend="claude", client=client)
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["max_tokens"], 2000)
        self.assertEqual(kwargs["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(kwargs["output_config"],
                         {"effort": "low", "format": {"type": "json_schema", "schema": LISTING_SCHEMA}})
        self.assertEqual(kwargs["system"], PARSER_SYSTEM_PROMPT)
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": TEXT}])
        self.assertNotIn("temperature", kwargs)
        self.assertNotIn("thinking", kwargs)

    def test_refusal_falls_back_to_rules(self):
        client, _ = fake_client(stop_reason="refusal", text="")
        listing = parse_claude("have a lightning charger, need a desk lamp", client)
        self.assertEqual(listing.parser, "rules")
        self.assertEqual(listing.have.attributes, {"connector": "lightning"})

    def test_exception_falls_back_to_rules(self):
        client, _ = fake_client(error=RuntimeError("connection reset"))
        self.assertEqual(parse_claude("need a ti-84", client).parser, "rules")

    def test_invalid_output_falls_back_to_rules(self):
        for text in ("not json", json.dumps({**GOOD, "have": {**GOOD["have"], "category": "spaceship"}})):
            client, _ = fake_client(text=text)
            self.assertEqual(parse_claude("need a ti-84", client).parser, "rules")

    def test_schema_is_closed_and_complete(self):
        def objects(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    yield node
                for v in node.values():
                    yield from objects(v)
            elif isinstance(node, list):
                for v in node:
                    yield from objects(v)

        found = list(objects(LISTING_SCHEMA))
        self.assertGreaterEqual(len(found), 5)
        for obj in found:
            self.assertIs(obj["additionalProperties"], False)
            self.assertEqual(sorted(obj["required"]), sorted(obj["properties"]))

    def test_no_sdk_or_no_key_means_no_client(self):
        with mock.patch.object(parse, "anthropic", None):
            self.assertIsNone(parse.make_client())
        with mock.patch.object(parse, "anthropic", mock.Mock()), \
                mock.patch.object(keychain, "ensure_api_key", return_value=False):
            self.assertIsNone(parse.make_client())
            self.assertEqual(parse.parse("need a ti-84", backend="auto").parser, "rules")


class Keychain(unittest.TestCase):
    def test_reads_the_key_into_memory_without_printing_it(self):
        done = subprocess.CompletedProcess([], 0, stdout="sk-test-not-real\n", stderr="")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(subprocess, "run", return_value=done) as run, \
                mock.patch.dict("os.environ", {}, clear=True), redirect_stdout(out), redirect_stderr(err):
            self.assertTrue(keychain.ensure_api_key())
            import os
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "sk-test-not-real")
        self.assertEqual(run.call_args.args[0],
                         ["security", "find-generic-password", "-a", "hercules", "-s", "anthropic-api-key", "-w"])
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(out.getvalue() + err.getvalue(), "")

    def test_missing_key_or_no_security_tool(self):
        missing = subprocess.CompletedProcess([], 44, stdout="", stderr="could not be found")
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(subprocess, "run", return_value=missing):
                self.assertFalse(keychain.ensure_api_key())
            with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError):
                self.assertFalse(keychain.ensure_api_key())

    def test_an_env_key_wins_and_the_keychain_is_not_read(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}), mock.patch.object(subprocess, "run") as run:
            self.assertTrue(keychain.ensure_api_key())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
