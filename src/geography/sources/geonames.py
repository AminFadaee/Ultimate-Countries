import csv
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass

import requests

from geography.sources.http import TIMEOUT

CITIES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
CITIES_FILE = "cities15000.txt"
SECTION_OF_PLACE = "PPLX"
ID_COLUMN, NAME_COLUMN, FEATURE_CODE_COLUMN, COUNTRY_COLUMN, POPULATION_COLUMN = 0, 1, 7, 8, 14


@dataclass(frozen=True)
class GeoCity:
    geonames_id: str
    name: str
    population: int


def fetch_largest_cities(session: requests.Session, count: int) -> dict[str, list[GeoCity]]:
    response = session.get(CITIES_URL, timeout=TIMEOUT)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        text = archive.read(CITIES_FILE).decode("utf-8")

    by_country = defaultdict(list)
    for row in csv.reader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row[FEATURE_CODE_COLUMN] == SECTION_OF_PLACE:
            continue
        by_country[row[COUNTRY_COLUMN]].append(GeoCity(row[ID_COLUMN], row[NAME_COLUMN], int(row[POPULATION_COLUMN])))
    return {
        country: sorted(cities, key=lambda city: -city.population)[:count]
        for country, cities in by_country.items()
    }
