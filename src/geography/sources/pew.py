import io
import zipfile
from dataclasses import dataclass

import pandas as pd
import requests

from geography.models import Religion
from geography.sources.http import TIMEOUT

DATASET_URL = "https://www.pewresearch.org/wp-content/uploads/sites/20/2025/06/Religious-Composition-2010-2020-dataset.zip"
PERCENTAGES_FILE = "Religious Composition 2010-2020 (percentages).csv"
COUNTRY_LEVEL = 1

RELIGIONS = {
    "Christians": "Christianity",
    "Muslims": "Islam",
    "Religiously_unaffiliated": "Unaffiliated",
    "Buddhists": "Buddhism",
    "Hindus": "Hinduism",
    "Jews": "Judaism",
    "Other_religions": "Other religions",
}


@dataclass(frozen=True)
class ReligionData:
    year: int
    by_code: dict[str, list[Religion]]
    by_name: dict[str, list[Religion]]

    def find(self, ccn3: str | None, name: str) -> list[Religion]:
        return self.by_code.get(ccn3 or "") or self.by_name.get(name.casefold(), [])


def fetch_religions(session: requests.Session) -> ReligionData:
    response = session.get(DATASET_URL, timeout=TIMEOUT)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        name = next(n for n in archive.namelist() if n.endswith(PERCENTAGES_FILE))
        table = pd.read_csv(archive.open(name), encoding="utf-8-sig")

    table = table[table["Level"] == COUNTRY_LEVEL]
    year = int(table["Year"].max())
    by_code, by_name = {}, {}
    for row in table[table["Year"] == year].to_dict("records"):
        shares = sorted(
            (
                Religion(label, round(float(row[column]), 1))
                for column, label in RELIGIONS.items()
                if float(row[column]) > 0
            ),
            key=lambda religion: -religion.percent,
        )
        by_code[f"{int(row['Countrycode']):03d}"] = shares
        by_name[str(row["Country"]).casefold()] = shares
    return ReligionData(year, by_code, by_name)
