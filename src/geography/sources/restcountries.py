from datetime import UTC, datetime

import requests

from geography.models import Country, Currency, Field, ListedCapital, Provenance, Source
from geography.sources.http import TIMEOUT

API_URL = "https://api.restcountries.com/countries/v5"
PAGE_SIZE = 100
DISPUTED = "disputed"

MEMBERSHIPS = {
    "eu": "European Union",
    "eurozone": "Eurozone",
    "schengen": "Schengen Area",
    "nato": "NATO",
    "commonwealth": "Commonwealth of Nations",
    "oecd": "OECD",
    "g7": "G7",
    "g20": "G20",
    "brics": "BRICS",
    "opec": "OPEC",
    "african_union": "African Union",
    "asean": "ASEAN",
    "arab_league": "Arab League",
}

FIELDS = (
    Field.IDENTITY,
    Field.POPULATION,
    Field.DEMONYM,
    Field.CURRENCIES,
    Field.GOVERNMENT,
    Field.MEMBERSHIPS,
    Field.REGION,
)


class RestCountries:
    def __init__(self, api_key: str, session: requests.Session):
        self.session = session
        self.session.headers["Authorization"] = f"Bearer {api_key}"

    def fetch_all(self) -> list[dict]:
        countries, offset = [], 0
        while True:
            response = self.session.get(
                API_URL,
                params={"limit": PAGE_SIZE, "offset": offset},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()["data"]
            countries.extend(data["objects"])
            if not data["meta"]["more"]:
                return countries
            offset += PAGE_SIZE


def updated_on(raw: dict) -> str | None:
    timestamp = raw.get("_meta", {}).get("lastUpdatedTimestamp")
    return datetime.fromtimestamp(timestamp, UTC).date().isoformat() if timestamp else None


def to_country(raw: dict, qid: str) -> Country:
    codes = raw["codes"]
    classification = raw["classification"]
    demonym = raw["demonyms"].get("eng", {})
    sovereign = classification["sovereign"]
    provenance = Provenance(Source.RESTCOUNTRIES, updated_on(raw))
    return Country(
        id=qid,
        restcountries_id=raw["uuid"],
        name=raw["names"]["common"],
        official_name=raw["names"]["official"],
        alpha_2=codes["alpha_2"] or None,
        alpha_3=codes["alpha_3"] or None,
        ccn3=codes["ccn3"] or None,
        wikipedia_url=raw["links"].get("wikipedia") or None,
        sovereign=sovereign,
        disputed=classification["disputed"] or classification["dependency_type"] == DISPUTED,
        un_member=classification["un_member"],
        un_observer=classification["un_observer"],
        dependency_type=classification["dependency_type"] or None,
        parent_alpha_3=raw["parent"]["alpha_3"] or None,
        region=raw["region"] or None,
        subregion=raw["subregion"] or None,
        area_km2=raw["area"]["kilometers"] or None,
        population=raw["population"] or None,
        demonym=demonym.get("m") or None,
        demonym_female=demonym.get("f") if demonym.get("f") != demonym.get("m") else None,
        government=raw.get("government_type") or None,
        listed_capitals=[
            ListedCapital(capital["name"], capital["coordinates"].get("lat"), capital["coordinates"].get("lng"))
            for capital in raw["capitals"]
        ],
        continents=raw["continents"],
        currencies=[Currency(c["code"], c["name"], c["symbol"]) for c in raw["currencies"]],
        memberships=[
            label for key, label in MEMBERSHIPS.items() if sovereign and raw["memberships"].get(key)
        ],
        provenance={field: provenance for field in FIELDS},
    )


def flag_url(raw: dict) -> str | None:
    return raw["flag"]["url_svg"] or None
