"""Read ASU's public class-search data into Sections.

`parse` and `load_fixture` work offline. `fetch` calls the live public API and is
never used by the tests or the simulator.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from .models import Meeting, Section, parse_clock

API = "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search/classes"

_DAY_FIELDS = {"MON": "MO", "TUES": "TU", "WED": "WE", "THURS": "TH", "FRI": "FR", "SAT": "SA", "SUN": "SU"}


def parse_class(row: dict) -> Section:
    clas = row["CLAS"]
    days = frozenset(code for name, code in _DAY_FIELDS.items() if clas.get(name) == "Y")
    meetings: tuple[Meeting, ...] = ()
    if days and clas.get("STARTTIME") and clas.get("ENDTIME"):
        meetings = (Meeting(days, parse_clock(clas["STARTTIME"]), parse_clock(clas["ENDTIME"])),)
    # SSRCOMPONENT is "LEC" on lab rows and empty on some Tempe labs in the real data;
    # COMPONENTPRIMARY is the row's own component.
    component = clas.get("COMPONENTPRIMARY") or clas.get("SSRCOMPONENT") or ""
    instructors = [name for name in (clas.get("INSTRUCTORSLIST") or []) if name != "Staff"]
    return Section(
        class_nbr=clas["CLASSNBR"],
        course=f"{clas['SUBJECT']} {clas['CATALOGNBR']}",
        section=clas["CLASSSECTION"],
        component=component,
        meetings=meetings,
        seats_taken=int(clas["ENRLTOT"]),
        capacity=int(clas["ENRLCAP"]),
        campus=clas.get("CAMPUS") or "",
        instructor=", ".join(instructors) or None,
    )


def parse(data: dict) -> list[Section]:
    return [parse_class(row) for row in data["classes"]]


def load_fixture(path: str | Path) -> list[Section]:
    return parse(json.loads(Path(path).read_text()))


def search_url(subject: str, catalog_nbr: str, term: str) -> str:
    query = {
        "refine": "Y",
        "campusOrOnlineSelection": "A",
        "honors": "F",
        "promod": "F",
        "searchType": "all",
        "subject": subject,
        "catalogNbr": catalog_nbr,
        "term": term,
    }
    return f"{API}?{urllib.parse.urlencode(query)}"


def fetch(subject: str, catalog_nbr: str, term: str, timeout: float = 20.0) -> list[Section]:
    """Live call to the public API. Not used offline; the API wants the literal 'Bearer null'."""
    request = urllib.request.Request(
        search_url(subject, catalog_nbr, term), headers={"Authorization": "Bearer null"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return parse(json.load(response))
