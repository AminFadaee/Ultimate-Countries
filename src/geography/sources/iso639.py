import csv
import io
from dataclasses import dataclass

import requests

from geography.sources.http import TIMEOUT

DOWNLOADS_URL = "https://iso639-3.sil.org/sites/iso639-3/files/downloads"
CODES_URL = f"{DOWNLOADS_URL}/iso-639-3.tab"
MACROLANGUAGES_URL = f"{DOWNLOADS_URL}/iso-639-3-macrolanguages.tab"
CONSTRUCTED = "C"


@dataclass(frozen=True)
class LanguageCodes:
    names: dict[str, str]
    part3_by_part1: dict[str, str]
    macrolanguage_of: dict[str, str]
    constructed: frozenset[str]

    def part3(self, code: str) -> str:
        base = code.split("_")[0].casefold()
        return self.part3_by_part1.get(base, base)


def read_table(session: requests.Session, url: str) -> list[dict[str, str]]:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter="\t"))


def fetch_codes(session: requests.Session) -> LanguageCodes:
    codes = read_table(session, CODES_URL)
    macrolanguages = read_table(session, MACROLANGUAGES_URL)
    return LanguageCodes(
        names={row["Id"]: row["Ref_Name"] for row in codes},
        part3_by_part1={row["Part1"]: row["Id"] for row in codes if row["Part1"]},
        macrolanguage_of={row["I_Id"]: row["M_Id"] for row in macrolanguages if row["I_Status"] == "A"},
        constructed=frozenset(row["Id"] for row in codes if row["Language_Type"] == CONSTRUCTED),
    )
