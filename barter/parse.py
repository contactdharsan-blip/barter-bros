"""Free text -> Listing.

Two backends:
- `parse_rules`: keyword and regex rules. Always available, offline, deterministic.
- `parse_claude`: the Anthropic API (claude-opus-5-5, structured JSON output, server-side
  fallback). Used only when a client is supplied or an API key is configured. On a refusal,
  any exception, or output that doesn't validate, it falls back to the rules.

`parse(text, backend="rules")` is what the simulator calls; the simulator never touches the
network unless it is run with `--claude`.
"""

from __future__ import annotations

import json
import re

from .models import CATEGORIES, CONDITIONS, Item, Listing, Want

try:  # the SDK is optional: the rules parser and the tests work without it
    import anthropic
except ImportError:  # pragma: no cover - depends on the environment
    anthropic = None

MODEL = "claude-opus-5-5"

# ---------------------------------------------------------------- rules

_WANT = re.compile(
    r"\b(need|needs|needing|want|wants|wanting|looking for|lf|iso|in search of|would take|"
    r"would like|'d like|would love|'d love|in exchange for|trade (?:it |them )?for|swap (?:it |them )?for|in return|"
    # asking around: "anyone got a calc?", "can someone give me a calculator?", "who has a lamp?"
    r"any(?:one|body) (?:got|have|has)|does any(?:one|body) have|some(?:one|body) (?:got|have|has)|"
    r"who(?:'s got| has| got)|(?:can|could) (?:some|any)(?:one|body) (?:give|lend|spare) me|give me)\b"
)
# "I don't have a charger" names something the student lacks: a want, never the item they give.
_NOT_HAVE = re.compile(r"\b(?:don'?t|do not|doesn'?t|haven'?t|have not|no longer) (?:have|own|got)\b")
# "I want to get rid of my desk chair": a want word that introduces something to give.
_GIVE_INTENT = re.compile(
    r"\b(?:want|wanna|need|would like|'d like|trying|looking|hoping) to "
    r"(?:get rid of|give away|sell|offload|unload|part with|pass on)\b"
)
_HAVE = re.compile(
    r"\b(i can give up|i can give|can give up|can give|i'll give|i will give|i could give|i could part with|"
    r"i'd give|i would give|i'd trade|i'd swap|i'd part with|"
    r"could part with|can part with|i can trade|can trade|i can swap|can swap|"
    r"i have|i've got|ive got|i got|have|has|got|giving away|giving|give away|selling|offering|"
    r"trading|free to (?:a )?good home|getting rid of|get rid of)\b"
)
# "cheap", "as cheap as possible", "low cost": the student wants to give up as little as they can.
# Listing has no field for that (floor is the opposite: the least they'll accept), so the rules
# read it and drop it, rather than letting it become an item called "cheap".
_CHEAP = re.compile(r"\b(?:as )?cheap(?:ly)?(?: as possible)?\b|\blow[\s-]?cost\b")
# Saying it is free, in so many words. A listing with an item and no wants is posted as a giveaway
# only when this matches; otherwise the agent asks first (`says_giveaway`).
_GIVEAWAY = re.compile(
    r"\b(giving (?:it |them )?away|give (?:it |them )?away|free|for free|no need for anything|free to (?:a )?good home|"
    r"(?:do not|don'?t) need anything|(?:do not|don'?t) want anything|nothing (?:back|in return)|for nothing)\b"
)
_FLOOR = re.compile(
    r"(?:at least|no less than|min(?:imum)?(?: of)?|worth(?: at least)?|floor(?: of)?|>=|≥)\s*\$\s?(\d+(?:\.\d+)?)"
    r"|\$\s?(\d+(?:\.\d+)?)\s*(?:or more|minimum|min)\b"
)
_FOR = re.compile(r"\s(?:in exchange for|in return for|for)\s+(?=(?:a|an|any|some|your|someone's)\b)")
_CLAUSE = re.compile(
    r"[,;.!?\n]+|\s+but\s+|\s+and\s+(?=(?:i\b|i'|need|want|have|got|looking|in exchange))"
    r"|\s+(?=(?:i\s+)?(?:need|looking for)\b)"  # "have a skateboard text me ... need a bike"
)

_CALCULATOR = re.compile(r"\bcalc(?:ulator)?s?\b|\bti[\s-]?(?:84|83|89|30|36|nspire)|\bcasio\s?fx")
_CASE = re.compile(r"\b(?:phone )?cases?\b")
_PHONE = re.compile(
    r"\b(iphone)\s?(\d{1,2})(\s?(?:pro max|pro|plus|mini))?\b|\b(pixel)\s?(\d{1,2})(\s?(?:pro|a))?\b"
    r"|\b(?:galaxy|samsung)\s?s\s?(\d{2})(\s?(?:ultra|plus|\+))?"
)
_CHARGER = re.compile(r"\bcharg(?:er|ers|ing cable|ing brick|ing block)\b|\bcable\b|\bpower (?:brick|adapter)\b|\bwall plug\b|\bbrick\b")
_TEXTBOOK = re.compile(r"\btext ?books?\b|\bbooks?\b|\bedition\b|\blab manual\b|\bcoursepack\b")
_CLOTHING = re.compile(
    r"\b(hoodie|sweatshirt|t-shirt|tee|shirt|jacket|coat|jeans|pants|shorts|sweater|dress|skirt|sneakers|shoes)\b"
)
_FURNITURE = re.compile(
    r"\b(desk lamp|floor lamp|desk chair|desk|chair|lamp|bookshelf|shelf|futon|dresser|table|mini[\s-]?fridge|fridge|rug|mattress|couch|ottoman)\b"
)
_CONNECTOR = [
    (re.compile(r"\busb[\s-]?c\b|\btype[\s-]?c\b|\busbc\b"), "usb-c"),
    (re.compile(r"\blightning\b"), "lightning"),
    (re.compile(r"\bmicro[\s-]?usb\b"), "micro-usb"),
]
_WATTS = re.compile(r"\b(\d{1,3})\s?-?\s?w(?:atts?)?\b")
_COURSE = re.compile(r"\b([a-z]{3}|chem|math|psych|phys|stats)\s?-?(\d{3})\b")
_SUBJECT = {"chem": "CHM", "math": "MAT", "psych": "PSY", "phys": "PHY", "stats": "STP"}  # what students say
_EDITION = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:ed\.?|edition)(?=\W|$)")
_TITLE = re.compile(r"[\"“']([^\"”']{3,})[\"”']")
_TI = re.compile(r"\bti[\s-]?(84|83|89|30|36|nspire)")
_CASIO = re.compile(r"\bcasio\s?(fx[\s-]?\w+)")
_SIZE = re.compile(r"\bsize\s+(xxs|xs|s|m|l|xl|xxl|\d{1,2})\b|\b(xxs|xs|xl|xxl)\b|\b(small|medium|large)\b")
_NEW = re.compile(r"\b(brand new|like new|new|unopened|sealed|mint)\b")
_FAIR = re.compile(r"\b(fair|worn|beat up|beat-up|scratched|rough|well[\s-]used|old)\b")
_GOOD = re.compile(r"\b(good|used|decent|gently used)\b")
_FILLER = re.compile(
    r"\b(i|i'd|i'll|i've|ive|have|has|got|need|needs|want|wants|a|an|the|my|some|any|for|of|to|and|or|it|"
    r"giving|away|give|free|looking|selling|offering|trading|that|this|one|in|good|new|used|condition|"
    r"never|again|open|will|would|take|like|anything|something|worth|me|back|return|exchange|if|possible|"
    r"please|really|just|old|spare|extra|from|last|semester|year|is|it's|its|with|nothing|"
    r"can|could|up|part|swap|trade)\b"
)


def _phone_model(text: str) -> str | None:
    m = _PHONE.search(text)
    if not m:
        return None
    if m.group(1):
        return f"iPhone {m.group(2)}{(' ' + m.group(3).strip().title()) if m.group(3) else ''}"
    if m.group(4):
        return f"Pixel {m.group(5)}{(' ' + m.group(6).strip().title()) if m.group(6) else ''}"
    return f"Galaxy S{m.group(7)}{(' ' + m.group(8).strip().title().replace('+', 'Plus')) if m.group(8) else ''}"


def _condition(text: str) -> str | None:
    if re.search(r"\blike new\b|\bbrand new\b", text) or _NEW.search(text):
        return "new"
    if _FAIR.search(text):
        return "fair"
    if _GOOD.search(text):
        return "good"
    return None


# Contact details and identity never reach the wire. Emails, links, @handles and phone numbers are
# removed before anything is parsed; an unknown item's name stops at the first word that starts
# talking about the student ("dm me", "my insta", "I live in", "from jordan").
_CONTACT = re.compile(
    r"\S+@\S+|https?://\S+|www\.\S+|@\w[\w.]*|\+?\d[\d\s().-]{6,}\d"
)
_ABOUT_ME = re.compile(
    r"\b(?:dm|dms|pm|text|txt|call|email|e-mail|mail|contact|reach|hmu|message|msg|insta|instagram|ig|"
    r"snap|snapchat|discord|venmo|zelle|cashapp|number|phone number|name|named|live|living|room|dorm|"
    r"apt|apartment|address|from|i'm|im|i am|this is|it's me)\b"
)


# "snap alice99", "my insta is ana.r", "ig: ana_r", "add me on discord anar#1234": the name after an app.
_APP_HANDLE = re.compile(
    r"\b(?:snap(?:chat)?|sc|insta(?:gram)?|ig|discord|whatsapp|telegram|tiktok|twitter|venmo|cash ?app|zelle)\b"
    r"(?:\s*(?:is|at|me|on|handle|username|user|name|:|-))*\s*"
    r"(?!(?:too|also|and|or|the|now|here|there|later|then|either|instead)\b)[\w.#@-]*\w",
    re.I,
)


def scrub_contact(text: str) -> tuple[str, bool]:
    """(text with emails, links, @handles, app usernames and phone numbers removed, whether anything was)."""
    clean = _CONTACT.sub("[contact left out]", _APP_HANDLE.sub("[contact left out]", text))
    return clean, clean != text


def says_giveaway(text: str) -> bool:
    """Whether the student said, in so many words, that they want nothing back."""
    return bool(_GIVEAWAY.search(text.lower().replace("\u2019", "'")))


def _kind(text: str) -> str | None:
    if cut := _ABOUT_ME.search(text):
        text = text[: cut.start()]
    words = [w for w in re.sub(r"[^a-z0-9\- ]", " ", _FILLER.sub(" ", text)).split() if w]
    return " ".join(words[:3]) or None


_NOTHING = re.compile(r"\bnothing\b|\bdon'?t have anything\b|\bno(?:thing)? to (?:trade|give|offer)\b|\bhave nothing\b")


def _thing(text: str) -> tuple[str, dict] | None:
    """Category and attributes for one phrase, or None if it names nothing."""
    if _NOTHING.search(text):
        return None
    if _CALCULATOR.search(text):
        m, c = _TI.search(text), _CASIO.search(text)
        model = f"TI-{m.group(1).title() if m.group(1) == 'nspire' else m.group(1)}" if m else (
            f"Casio {c.group(1).upper().replace(' ', '-')}" if c else None)
        return "calculator", {"model": model}
    if _CASE.search(text) and not re.search(r"\bpencil case\b|\bbriefcase\b", text):
        return "phone_case", {"model": _phone_model(text)}
    if _CHARGER.search(text) or any(rx.search(text) for rx, _ in _CONNECTOR):
        connector = next((name for rx, name in _CONNECTOR if rx.search(text)), None)
        phone = _phone_model(text)
        if connector is None and phone and phone.startswith("iPhone"):
            connector = "usb-c" if int(phone.split()[1]) >= 15 else "lightning"
        w = _WATTS.search(text)
        return "charger", {"connector": connector, "watts": int(w.group(1)) if w else None}
    if _TEXTBOOK.search(text) or (_COURSE.search(text) and _EDITION.search(text)):
        course, edition, title = _COURSE.search(text), _EDITION.search(text), _TITLE.search(text)
        return "textbook", {
            "course": f"{_SUBJECT.get(course.group(1), course.group(1).upper())} {course.group(2)}" if course else None,
            "title": title.group(1).strip() if title else None,
            "edition": edition.group(1) if edition else None,
        }
    if m := _CLOTHING.search(text):
        s = _SIZE.search(text)
        size = None
        if s:
            raw = next(g for g in s.groups() if g)
            size = {"small": "S", "medium": "M", "large": "L"}.get(raw, raw.upper())
        return "clothing", {"kind": m.group(1), "size": size}
    if m := _FURNITURE.search(text):
        return "furniture", {"kind": m.group(1)}
    kind = _kind(text)
    return ("other", {"kind": kind}) if kind else None


def _split_clauses(text: str) -> list[tuple[str, str]]:
    """[(role, clause)], role 'want' or 'have'.

    A clause that states a floor is dropped here (the floor is read separately). A clause with no
    have/want marker of its own takes the previous role: if it names a kind of thing it is its own
    item ("..., a lightning cable too"), otherwise it refines the previous clause ("..., size M").
    """
    out: list[list[str]] = []
    role = None
    for clause in (c.strip() for c in _CLAUSE.split(text)):
        if clause and _FLOOR.search(clause):
            # "I have a desk lamp worth $50": the floor is read separately, and what comes before it
            # stays. What follows a floor ("at least $41 for the calculator") is about the same item.
            clause = clause[: _FLOOR.search(clause).start()].strip()
            if not (_WANT.search(clause) or _HAVE.search(clause)):
                continue
        if not clause:
            continue
        w, h = _WANT.search(clause), _HAVE.search(clause)
        if g := _GIVE_INTENT.search(clause):
            role = "have"
            out.append([role, clause[g.end():]])
        elif n := _NOT_HAVE.search(clause):
            role = "want"
            out.append([role, clause[n.end():]])
        elif w and (not h or w.start() <= h.start()):
            role = "want"
            out.append([role, clause[w.end():]])
        elif h:
            role = "have"
            if f := _FOR.search(clause, h.end()):  # "trading my TI-84 for a lightning charger"
                out.append(["have", clause[: f.start()]])
                role, clause = "want", clause[f.end():]
            out.append([role, clause])
        else:
            found = _thing(clause)
            previous = _thing(out[-1][1]) if out else None
            if found and found[0] != "other" and (previous is None or previous[0] != found[0]):
                out.append([role or "have", clause])
            elif out:  # same kind of thing, or no thing at all: a detail of the previous clause
                out[-1][1] += " " + clause
    return [(r, c) for r, c in out]


def parse_rules(text: str) -> Listing:
    low = _CHEAP.sub(" ", _CONTACT.sub(" ", text.lower().replace("\u2019", "'")))
    floor = None
    if m := _FLOOR.search(low):
        floor = float(m.group(1) or m.group(2))
    have: Item | None = None
    wants: list[Want] = []
    for role, clause in _split_clauses(low):
        if role == "have" and have is None:
            found = _thing(clause)
            if found:
                have = Item(found[0], found[1], _condition(clause) or "good")
        elif role == "want":
            for part in re.split(r"\s+or\s+|\s+and\s+|/", clause):  # "a TI-84, a lamp and a charger"
                found = _thing(part)
                if found:
                    wants.append(Want(found[0], found[1], _condition(part)))
    return Listing(have=have, wants=wants, floor=floor, parser="rules", text=text)


# ---------------------------------------------------------------- Claude

_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_ATTRS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["connector", "watts", "model", "course", "title", "edition", "kind", "size"],
    "properties": {
        "connector": {"anyOf": [{"type": "string", "enum": ["usb-c", "lightning", "micro-usb"]}, {"type": "null"}]},
        "watts": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "model": _STR,
        "course": _STR,
        "title": _STR,
        "edition": _STR,
        "kind": _STR,
        "size": _STR,
    },
}
_TEXT_CATEGORIES = [c for c in CATEGORIES if c != "section"]  # sections come from ASU data, not text
LISTING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["have", "wants", "floor"],
    "properties": {
        "have": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["category", "attributes", "condition"],
                    "properties": {
                        "category": {"type": "string", "enum": _TEXT_CATEGORIES},
                        "attributes": _ATTRS,
                        "condition": {"type": "string", "enum": list(CONDITIONS)},
                    },
                },
                {"type": "null"},
            ]
        },
        "wants": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["category", "attributes", "min_condition"],
                "properties": {
                    "category": {"type": "string", "enum": _TEXT_CATEGORIES},
                    "attributes": _ATTRS,
                    "min_condition": {"anyOf": [{"type": "string", "enum": list(CONDITIONS)}, {"type": "null"}]},
                },
            },
        },
        "floor": {"anyOf": [{"type": "number"}, {"type": "null"}]},
    },
}

PARSER_SYSTEM_PROMPT = """\
You read one message from a college student to their barter agent and return what they have and what they want, as JSON matching the schema.

- have: the one item they are offering, or null if they offer nothing. condition is new, good or fair; use good when they don't say.
- wants: every item they would accept. An empty list means they are giving the item away.
- floor: the least value in US dollars they said they would accept for their item (e.g. "for anything worth at least $30" -> 30), else null.
- category is one of charger, phone_case, textbook, calculator, furniture, clothing, other.
- attributes: set only what the student actually said; every other attribute is null.
  - charger: connector is usb-c, lightning or micro-usb ("type-c" is usb-c); watts as an integer.
  - phone_case: model like "iPhone 15" or "Pixel 8 Pro".
  - textbook: course like "BIO 181", title, edition as a number string like "3".
  - calculator: model family like "TI-84" or "TI-Nspire".
  - clothing: kind (hoodie, jacket...) and size (S, M, L, XL...). furniture and other: kind.
- In a want, min_condition is set only if they asked for a condition ("must be new"); otherwise null.
- Never invent a preference the student did not state. Leave names, contact details and reasons out."""


def _attrs(raw: dict) -> dict:
    return {k: v for k, v in raw.items() if v is not None}


def _listing_from_json(data: dict, text: str) -> Listing:
    have = None
    if data["have"] is not None:
        h = data["have"]
        have = Item(h["category"], _attrs(h["attributes"]), h["condition"])
    wants = [Want(w["category"], _attrs(w["attributes"]), w["min_condition"]) for w in data["wants"]]
    floor = float(data["floor"]) if data["floor"] is not None else None
    return Listing(have=have, wants=wants, floor=floor, parser="claude", text=text)


def parse_claude(text: str, client) -> Listing:
    """Ask Claude. Any refusal, error or invalid output -> the rules parser instead."""
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": LISTING_SCHEMA}},
            system=PARSER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        if resp.stop_reason in ("refusal", "max_tokens"):  # declined, or truncated JSON
            return parse_rules(text)
        body = next(block.text for block in resp.content if getattr(block, "type", None) == "text")
        return _listing_from_json(json.loads(body), text)
    except Exception:  # noqa: BLE001 - every failure means "use the rules", by design
        return parse_rules(text)


def make_client():
    """An Anthropic client when the SDK is installed and a key is configured, else None."""
    from . import keychain

    if anthropic is None or not keychain.ensure_api_key():
        return None
    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment at runtime


def parse(text: str, backend: str = "rules", client=None) -> Listing:
    """backend: "rules" (offline), "claude" (needs `client`), or "auto" (Claude if a key is configured)."""
    if backend == "auto" and client is None:
        client = make_client()
    if backend in ("claude", "auto") and client is not None:
        return parse_claude(text, client)
    return parse_rules(text)
