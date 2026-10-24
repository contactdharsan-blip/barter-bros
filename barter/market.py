"""A busier demo market: twelve invented bots for the Exchange view.

Same shape as a scenario's `people`, so the service seeds them the same way. It lives here, not
in `scenarios/`, because the scenario tests count the scripted runs there.

What forms when they're seeded: a 2-way loop (calculator <-> mini fridge), a 3-way loop (hoodie
-> CHM 113 textbook -> desk chair), and a chain from a bookshelf giveaway that ends with someone
who has nothing to give. Four listings wait. None of these items fits the book/charger/case
demo, so the market never takes Jordan's loop. Order matters: the exchange matches as each
listing arrives, so the chain's last person lists before the middle one. Every market bot answers
the handoff "later" (their meetup is on Saturday), so their loops stay on the graph instead of
finishing in a second.
"""

LATER = {"handoff": "later"}

MARKET: list[dict] = [
    {"name": "Avery Lin", "contact": "avery.lin@example.edu", "says": "Have a TI-84 calculator, good condition. Need a mini fridge.",
     "reasons": "Moving off campus.", "answers": LATER},
    {"name": "Bo Patel", "contact": "+1 480 555 0142", "says": "Have a mini fridge. Looking for a TI-84 calculator.",
     "reasons": "MAT 265 starts in January.", "answers": LATER},
    {"name": "Cam Ortiz", "contact": "cam.ortiz#4410", "says": "Have a hoodie, size M. Need a desk chair.",
     "reasons": "My chair broke.", "answers": LATER},
    {"name": "Dana Webb", "contact": "dana.webb@example.edu", "says": "Have a desk chair. Need a CHM 113 textbook.",
     "reasons": "Switched into CHM 113.", "answers": LATER},
    {"name": "Eli Novak", "contact": "+1 602 555 0199", "says": "Have a CHM 113 textbook, 2nd edition. Want a hoodie, size M.",
     "reasons": "Passed CHM 113.", "answers": LATER},
    {"name": "Fern Ruiz", "contact": "fern.ruiz@example.edu", "says": "Giving away a bookshelf for free.",
     "reasons": "Graduating in December.", "answers": LATER},
    {"name": "Hana Sato", "contact": "+1 480 555 0175", "says": "Need a lightning charger.",
     "reasons": "Left mine on a plane.", "answers": LATER},
    {"name": "Gus Adler", "contact": "gus.adler#0921", "says": "Need a bookshelf. Have a Lightning charger.",
     "reasons": "Switched to an Android phone.", "answers": LATER},
    {"name": "Ivy Moreau", "contact": "ivy.moreau@example.edu", "says": "Have a Pixel 8 case. Need a TI-Nspire calculator.",
     "reasons": "Upgraded my phone.", "answers": LATER},
    {"name": "Jae Kim", "contact": "jae.kim#7781", "says": "Have a futon. Need a rug.",
     "reasons": "My roommate brought a couch.", "answers": LATER},
    {"name": "Kai Mendes", "contact": "+1 602 555 0108", "says": "Have a jacket, size L. Want a backpack.",
     "reasons": "Strap snapped on mine.", "answers": LATER},
    {"name": "Lou Becker", "contact": "lou.becker@example.edu", "says": "Have a Casio fx-991 calculator. Looking for a TI-84.",
     "reasons": "My professor requires a TI-84.", "answers": LATER},
]
