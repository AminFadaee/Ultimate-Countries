from datetime import date

import requests

from geography.sources.http import TIMEOUT

COUNTRIES_URL = "https://api.worldbank.org/v2/country"
AGGREGATES = "Aggregates"
NOT_CLASSIFIED = "Not classified"
MILITARY_SPENDING = "MS.MIL.XPND.GD.ZS"
POPULATION = "SP.POP.TOTL"
MAX_DATA_AGE_YEARS = 5


def fetch_income_groups(session: requests.Session) -> dict[str, str]:
    response = session.get(COUNTRIES_URL, params={"format": "json", "per_page": 500}, timeout=TIMEOUT)
    response.raise_for_status()
    _, countries = response.json()
    return {
        country["iso2Code"]: country["incomeLevel"]["value"]
        for country in countries
        if country["region"]["value"] != AGGREGATES and country["incomeLevel"]["value"] != NOT_CLASSIFIED
    }


def fetch_latest(session: requests.Session, indicator: str) -> dict[str, tuple[float, int]]:
    response = session.get(
        f"{COUNTRIES_URL}/all/indicator/{indicator}",
        params={"format": "json", "per_page": 20000, "mrnev": 1},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    _, rows = response.json()
    oldest = date.today().year - MAX_DATA_AGE_YEARS
    return {
        row["countryiso3code"]: (row["value"], int(row["date"]))
        for row in rows
        if row["value"] is not None and row["countryiso3code"] and int(row["date"]) >= oldest
    }


def fetch_military_spending(session: requests.Session) -> dict[str, tuple[float, int]]:
    return {code: (round(value, 1), year) for code, (value, year) in fetch_latest(session, MILITARY_SPENDING).items()}


def fetch_populations(session: requests.Session) -> dict[str, tuple[int, int]]:
    return {code: (int(value), year) for code, (value, year) in fetch_latest(session, POPULATION).items()}
