import csv
import io
from dataclasses import dataclass

import requests

from geography.sources.http import TIMEOUT

MAIN_CSV_URL = "https://raw.githubusercontent.com/anki-geo/ultimate-geography/master/src/data/main.csv"


@dataclass(frozen=True)
class ScopeEntry:
    name: str
    alpha_2: str | None


def fetch_scope(session: requests.Session) -> list[ScopeEntry]:
    response = session.get(MAIN_CSV_URL, timeout=TIMEOUT)
    response.raise_for_status()
    rows = csv.DictReader(io.StringIO(response.text))
    return [
        ScopeEntry(row["country"], row["ISO"] or None)
        for row in rows
        if row["ISO"] or row["flag"]
    ]
