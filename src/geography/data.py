import logging
import pathlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import geopandas as gpd
import pandas as pd
import requests

from geography.sources.http import TIMEOUT

DATA_DIR = pathlib.Path(__file__).parent.resolve()
DATA_URL = "https://naciscdn.org/naturalearth/10m/cultural"
COUNTRIES_FILE = "ne_10m_admin_0_countries_lakes.zip"
DISPUTED_FILE = "ne_10m_admin_0_disputed_areas.zip"
REGIONS_FILE = "ne_10m_admin_1_states_provinces_lakes.zip"
MAP_UNITS_FILE = "ne_10m_admin_0_map_units.zip"
COUNTRY_NAME_COLUMNS = ("ADM0_A3", "ISO_A2_EH", "ADMIN", "NAME", "NAME_LONG")
SHAPE_NAME_COLUMNS = ("ADMIN", "NAME", "NAME_LONG")
MAP_UNIT_CODE_COLUMNS = ("GU_A3", "SU_A3", "ISO_A3_EH")
MAP_UNIT_NAME_COLUMNS = ("NAME", "GEOUNIT")
CHUNK_SIZE = 1024 * 1024

logger = logging.getLogger(__name__)


def is_up_to_date(path: pathlib.Path, url: str) -> bool:
    response = requests.head(url, allow_redirects=True, timeout=TIMEOUT)
    response.raise_for_status()
    last_modified = response.headers.get("Last-Modified")
    if not last_modified:
        return True
    return path.stat().st_mtime >= parsedate_to_datetime(last_modified).timestamp()


def is_younger_than(path: pathlib.Path, max_age: timedelta) -> bool:
    modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return datetime.now(UTC) - modified < max_age


def download_if_needed(filename: str) -> pathlib.Path:
    return download_file(f"{DATA_URL}/{filename}", DATA_DIR / filename)


def download_file(url: str, path: pathlib.Path, max_age: timedelta | None = None) -> pathlib.Path:
    if path.exists():
        if max_age is not None and is_younger_than(path, max_age):
            return path
        if max_age is None and is_up_to_date(path, url):
            return path

    logger.info("Downloading %s...", path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.part")
    try:
        with requests.get(url, stream=True, timeout=TIMEOUT) as response:
            response.raise_for_status()
            with open(partial, "wb") as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    f.write(chunk)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


@dataclass(frozen=True)
class Country:
    code: str
    core: gpd.GeoDataFrame
    disputed: gpd.GeoDataFrame
    regions: gpd.GeoDataFrame

    @property
    def iso_a2(self) -> str:
        return self.core["ISO_A2_EH"].iloc[0]


@dataclass(frozen=True)
class NaturalEarth:
    countries: gpd.GeoDataFrame
    disputed_areas: gpd.GeoDataFrame
    regions: gpd.GeoDataFrame
    map_units: gpd.GeoDataFrame

    @classmethod
    def load(cls) -> "NaturalEarth":
        return cls(
            countries=gpd.read_file(download_if_needed(COUNTRIES_FILE)),
            disputed_areas=gpd.read_file(download_if_needed(DISPUTED_FILE)),
            regions=gpd.read_file(download_if_needed(REGIONS_FILE)),
            map_units=gpd.read_file(download_if_needed(MAP_UNITS_FILE)),
        )

    @property
    def crs(self):
        return self.countries.crs

    def find_country(self, query: str) -> Country:
        needle = query.casefold()
        for column in COUNTRY_NAME_COLUMNS:
            matches = self.countries[self.countries[column].str.casefold() == needle]
            if not matches.empty:
                return self.country(matches["ADM0_A3"].iloc[0])
        raise LookupError(f"Unknown country: {query}")

    def shape_for(self, name: str, alpha_3: str | None) -> Country | None:
        if alpha_3:
            if (self.countries["ADM0_A3"] == alpha_3).any():
                return self.country(alpha_3)
            rows = self.countries[self.countries["ISO_A3_EH"] == alpha_3]
            if len(rows) == 1:
                return self.country(rows["ADM0_A3"].iloc[0])
            for column in MAP_UNIT_CODE_COLUMNS:
                rows = self.map_units[self.map_units[column] == alpha_3]
                if len(rows) == 1:
                    return self._standalone(rows)

        needle = name.casefold()
        for column in SHAPE_NAME_COLUMNS:
            rows = self.countries[self.countries[column].str.casefold() == needle]
            if len(rows) == 1:
                return self.country(rows["ADM0_A3"].iloc[0])
        for column in MAP_UNIT_NAME_COLUMNS:
            rows = self.map_units[self.map_units[column].str.casefold() == needle]
            if len(rows) == 1:
                return self._standalone(rows)
        rows = self.disputed_areas[self.disputed_areas["BRK_NAME"].str.casefold() == needle]
        if len(rows) == 1:
            return self._standalone(rows)
        return None

    def country(self, code: str) -> Country:
        group = self.countries[self.countries["ADM0_A3_US"] == code]
        if group.empty:
            group = self.countries[self.countries["ADM0_A3"] == code]

        core_mask = (group["ADM0_A3"] == code) | (group["ADM0_TLC"] == code)
        disputed = group[~core_mask]

        extra = self.disputed_areas[self.disputed_areas["ADM0_A3_US"] == code]
        if not extra.empty:
            disputed = gpd.GeoDataFrame(
                pd.concat([disputed, extra], ignore_index=True),
                crs=self.crs,
            )

        regions = self.regions[self.regions["adm0_a3"] == code]
        return Country(code, group[core_mask], disputed, regions)

    def _standalone(self, rows: gpd.GeoDataFrame) -> Country:
        empty = rows.iloc[0:0]
        return Country(rows["ADM0_A3"].iloc[0], rows, empty, self.regions.iloc[0:0])
