import json
from dataclasses import dataclass
from datetime import date

import requests

from geography.sources.http import TIMEOUT

DBNOMICS_URL = "https://api.db.nomics.world/v22/series/IMF/WEO:latest"
GOVERNMENT_EXPENDITURE = "GGX_NGDP"
PAGE_SIZE = 1000
NAME_SEPARATOR = " – "


@dataclass(frozen=True)
class Spending:
    percent_of_gdp: float
    year: int


@dataclass(frozen=True)
class SpendingData:
    edition: str
    by_code: dict[str, Spending]
    by_name: dict[str, Spending]

    def find(self, alpha_3: str | None, name: str) -> Spending | None:
        return self.by_code.get(alpha_3 or "") or self.by_name.get(name.casefold())


def latest_value(periods: list[str], values: list, last_year: int) -> Spending | None:
    observed = [
        (int(period), float(value))
        for period, value in zip(periods, values)
        if value not in ("NA", None) and int(period) <= last_year
    ]
    if not observed:
        return None
    year, value = observed[-1]
    return Spending(round(value, 1), year)


def fetch_government_spending(session: requests.Session) -> SpendingData:
    response = session.get(
        DBNOMICS_URL,
        params={
            "dimensions": json.dumps({"weo-subject": [GOVERNMENT_EXPENDITURE]}),
            "observations": 1,
            "limit": PAGE_SIZE,
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    last_year = date.today().year
    by_code, by_name = {}, {}
    for series in payload["series"]["docs"]:
        spending = latest_value(series["period"], series["value"], last_year)
        if spending:
            by_code[series["dimensions"]["weo-country"]] = spending
            by_name[series["series_name"].split(NAME_SEPARATOR)[0].casefold()] = spending
    return SpendingData(payload["dataset"]["code"], by_code, by_name)
