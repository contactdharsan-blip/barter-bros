"""Plain data: items, wants, and the offer that goes on the wire.

Public (crosses the exchange): an item's category, attributes and condition, and a want's
category, attributes and minimum condition. Private (stays in the agent): everything in a
`Listing` beyond that, i.e. the value floor, private values, availability and reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONDITIONS = ("new", "good", "fair")  # best first
_RANK = {c: i for i, c in enumerate(CONDITIONS)}

# category -> the attribute keys it can carry
CATEGORIES: dict[str, tuple[str, ...]] = {
    "charger": ("connector", "watts"),
    "phone_case": ("model",),
    "textbook": ("course", "title", "edition"),
    "calculator": ("model",),
    "furniture": ("kind",),
    "clothing": ("kind", "size"),
    "other": ("kind",),
    "section": ("course", "class_nbr"),  # an ASU class section (barter/sections.py)
}
CONNECTORS = ("usb-c", "lightning", "micro-usb")

# Rough dollar value by category and condition. A hint for the student only; never a rule.
VALUE_TABLE: dict[str, dict[str, int]] = {
    "charger": {"new": 25, "good": 15, "fair": 8},
    "phone_case": {"new": 20, "good": 12, "fair": 6},
    "textbook": {"new": 90, "good": 60, "fair": 35},
    "calculator": {"new": 110, "good": 70, "fair": 45},
    "furniture": {"new": 80, "good": 50, "fair": 25},
    "clothing": {"new": 30, "good": 18, "fair": 8},
    "other": {"new": 15, "good": 10, "fair": 5},
    "section": {"new": 0, "good": 0, "fair": 0},
}

# Watts is a minimum when it appears in a want ("at least 20 W"); every other attribute must match.
_AT_LEAST = {"watts"}


# Words for the same kind of thing. Only the `kind` attribute (furniture, clothing, other) is read
# this loosely; connector, model, course and the rest stay exact.
_SAME_WORD = {"sweatshirt": "hoodie", "hoody": "hoodie", "tee": "t-shirt", "sofa": "couch"}


def _kind_words(kind: str) -> list[str]:
    """'2 Desk Lamps' -> ['desk', 'lamp']: lower case, no leading count, no plural s, synonyms merged."""
    words = [w for w in str(kind).lower().replace("-", " ").split() if not w.isdigit()]
    out = []
    for w in words:
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(_SAME_WORD.get(w, w))
    return out


def same_kind(have: str, wanted: str) -> bool:
    """A 'lamp' is a 'desk lamp' you didn't describe, and the other way round; a 'floor lamp' is not
    a 'desk lamp', and a 'desk' is not a 'desk chair'. Same last word, and one side's other words are
    all on the other side."""
    a, b = _kind_words(have), _kind_words(wanted)
    if not a or not b or a[-1] != b[-1]:
        return False
    return set(a[:-1]) <= set(b[:-1]) or set(b[:-1]) <= set(a[:-1])


def est_value(category: str, condition: str) -> int:
    return VALUE_TABLE.get(category, VALUE_TABLE["other"])[condition]


def _clean(attributes: dict) -> dict:
    return {k: v for k, v in attributes.items() if v not in (None, "", [])}


@dataclass(frozen=True)
class Item:
    category: str
    attributes: dict = field(default_factory=dict, hash=False)
    condition: str = "good"

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category!r}")
        if self.condition not in CONDITIONS:
            raise ValueError(f"unknown condition {self.condition!r}")
        object.__setattr__(self, "attributes", _clean(self.attributes))

    @property
    def est_value(self) -> int:
        return est_value(self.category, self.condition)

    def payload(self) -> dict:
        return {"category": self.category, "attributes": dict(self.attributes), "condition": self.condition}

    @classmethod
    def from_payload(cls, payload: dict) -> Item:
        if set(payload) != {"category", "attributes", "condition"}:
            raise ValueError(f"item payload has keys {sorted(payload)}")
        return cls(payload["category"], payload["attributes"], payload["condition"])

    def label(self) -> str:
        a = self.attributes
        name = self.category.replace("_", " ")
        if self.category == "charger":
            connector = {"usb-c": "USB-C", "lightning": "Lightning", "micro-usb": "Micro-USB"}.get(a.get("connector", ""), "")
            bits = [connector, f"{a['watts']} W" if "watts" in a else ""]
            name = " ".join(b for b in bits if b) + " charger"
        elif self.category == "phone_case":
            name = f"{a.get('model', 'phone')} case"
        elif self.category == "textbook":
            bits = [a.get("course", ""), a.get("title", ""), f"({a['edition']} ed.)" if "edition" in a else "", "textbook"]
            name = " ".join(b for b in bits if b)
        elif self.category == "calculator":
            name = a.get("model", "calculator")
        elif self.category == "section":
            name = f"{a.get('course', '')} section #{a.get('class_nbr', '?')}"
        elif "kind" in a:
            name = a["kind"] + (f", size {a['size']}" if "size" in a else "")
        return name.strip() + ("" if self.category == "section" else f" ({self.condition})")


@dataclass(frozen=True)
class Want:
    """What a student would take. An attribute left out means 'any'. A list means 'any of these'."""

    category: str
    attributes: dict = field(default_factory=dict, hash=False)
    min_condition: str | None = None

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category!r}")
        if self.min_condition is not None and self.min_condition not in CONDITIONS:
            raise ValueError(f"unknown condition {self.min_condition!r}")
        object.__setattr__(self, "attributes", _clean(self.attributes))

    def why_not(self, item: Item) -> str | None:
        """Why `item` doesn't satisfy this want, or None when it does. Public data only."""
        if item.category != self.category:
            return f"wrong kind of thing ({item.category}, wanted {self.category})"
        for key, wanted in self.attributes.items():
            have = item.attributes.get(key)
            if key in _AT_LEAST:
                if have is None or int(have) < int(wanted):
                    return f"{key} {have or 'unknown'} is below the {wanted} asked for"
            elif isinstance(wanted, (list, tuple)):
                if have not in wanted:
                    return f"{key} {have or 'unknown'} is not one of {', '.join(map(str, wanted))}"
            elif key == "kind" and have is not None:
                if not same_kind(have, wanted):
                    return f"{key} is {have}, wanted {wanted}"
            elif have is None or str(have).lower() != str(wanted).lower():
                return f"{key} is {have or 'unknown'}, wanted {wanted}"
        if self.min_condition is not None and _RANK[item.condition] > _RANK[self.min_condition]:
            return f"condition {item.condition} is worse than {self.min_condition}"
        return None

    def matches(self, item: Item) -> bool:
        return self.why_not(item) is None

    def payload(self) -> dict:
        return {"category": self.category, "attributes": dict(self.attributes), "min_condition": self.min_condition}

    @classmethod
    def from_payload(cls, payload: dict) -> Want:
        if set(payload) != {"category", "attributes", "min_condition"}:
            raise ValueError(f"want payload has keys {sorted(payload)}")
        attrs = {k: tuple(v) if isinstance(v, list) else v for k, v in payload["attributes"].items()}
        return cls(payload["category"], attrs, payload["min_condition"])

    def label(self) -> str:
        if not self.attributes and self.min_condition is None:
            return f"any {self.category.replace('_', ' ')}"
        bits = []
        for key, value in self.attributes.items():
            if isinstance(value, (list, tuple)):
                value = " or ".join(map(str, value))
            bits.append(f"{key} {'>= ' if key in _AT_LEAST else ''}{value}")
        if self.min_condition:
            bits.append(f"{self.min_condition} or better")
        return f"{self.category.replace('_', ' ')} ({', '.join(bits)})"


@dataclass
class Listing:
    """What a student told their agent. Private: only `have` and `wants` ever leave, as an Offer.

    `have` is None for a pure request ("need a desk, nothing to give"). `wants` empty means a
    giveaway. `floor` is the least the student would accept, in their own dollars, for `have`.
    """

    have: Item | None
    wants: list[Want]
    floor: float | None = None
    parser: str = "rules"  # which parser read the text: "rules" or "claude"
    text: str = ""

    @property
    def is_giveaway(self) -> bool:
        return self.have is not None and not self.wants


OFFER_KEYS = ("handle", "have", "wants")


@dataclass(frozen=True)
class Offer:
    """The whole of what an agent tells the exchange about a listing."""

    handle: str
    have: Item | None
    wants: tuple[Want, ...]

    @property
    def is_giveaway(self) -> bool:
        return self.have is not None and not self.wants

    def payload(self) -> dict:
        return {
            "handle": self.handle,
            "have": self.have.payload() if self.have else None,
            "wants": [w.payload() for w in self.wants],
        }

    @classmethod
    def from_payload(cls, payload: dict) -> Offer:
        if set(payload) != set(OFFER_KEYS):
            raise ValueError(f"offer payload must have exactly {OFFER_KEYS}, got {sorted(payload)}")
        have = Item.from_payload(payload["have"]) if payload["have"] is not None else None
        return cls(payload["handle"], have, tuple(Want.from_payload(w) for w in payload["wants"]))

    def wants_item(self, item: Item) -> bool:
        return any(w.matches(item) for w in self.wants)
