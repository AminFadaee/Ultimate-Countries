import json
import pathlib
from dataclasses import dataclass

import geopandas as gpd
from shapely.geometry import Point

from geography.data import NaturalEarth
from geography.render import EQUAL_AREA, get_mainland, get_nearby_parts

CRS = "EPSG:4326"
MIN_AREA_SHARE = 0.01
MIN_LINE_SHARE = 0.002
COAST_M = 10_000
POINT_M = 3_000
OUTLIER_FACTOR = 3


@dataclass(frozen=True)
class OurCountry:
    alpha_3: str
    name: str
    continent: str | None


def load_our_countries(countries_dir: pathlib.Path) -> dict[str, OurCountry]:
    found = {}
    for path in countries_dir.glob("*.json"):
        document = json.loads(path.read_text())
        if document["codes"]["alpha_3"]:
            alpha_3 = document["codes"]["alpha_3"]
            found[alpha_3] = OurCountry(alpha_3, document["name"], document["region"])
    return found


def main_territory(geometry) -> object:
    parts = gpd.GeoSeries([geometry], crs=CRS)
    return get_nearby_parts(parts, get_mainland(parts.explode(index_parts=False))).union_all()


class CountryLocator:
    def __init__(self, data: NaturalEarth, ours: dict[str, OurCountry]):
        self.shapes = data.countries.to_crs(EQUAL_AREA)
        self.geodetic = data.countries
        self.ours = ours

    def _country(self, row) -> OurCountry | None:
        return self.ours.get(row.ADM0_A3) or self.ours.get(row.ISO_A3)

    def countries_in(self, names: list[str]) -> gpd.GeoSeries:
        wanted = set(names)
        parts = gpd.GeoSeries(
            [main_territory(row.geometry) for row in self.geodetic.itertuples() if (country := self._country(row)) and country.name in wanted],
            crs=CRS,
        )
        areas = parts.to_crs(EQUAL_AREA).area
        typical = [area <= OUTLIER_FACTOR * (areas.sum() - area) for area in areas]
        return gpd.GeoSeries([parts[typical].union_all()], crs=CRS)

    def by_area(self, geometry: gpd.GeoSeries) -> list[OurCountry]:
        shape = geometry.to_crs(EQUAL_AREA).union_all()
        return self._ranked(lambda country: country.intersection(shape).area / shape.area, MIN_AREA_SHARE)

    def by_line(self, geometry: gpd.GeoSeries) -> list[OurCountry]:
        line = geometry.to_crs(EQUAL_AREA).union_all()
        return self._ranked(lambda country: country.intersection(line).length / line.length, MIN_LINE_SHARE)

    def by_coast(self, geometry: gpd.GeoSeries) -> list[OurCountry]:
        shape = geometry.to_crs(EQUAL_AREA).union_all().buffer(COAST_M)
        return self._ranked(lambda country: 1.0 if country.intersects(shape) else 0.0, 1.0)

    def at_point(self, longitude: float, latitude: float, radius_m: float = POINT_M) -> list[OurCountry]:
        area = gpd.GeoSeries([Point(longitude, latitude)], crs=CRS).to_crs(EQUAL_AREA).buffer(radius_m).iloc[0]
        return self._ranked(lambda country: 1.0 if country.intersects(area) else 0.0, 1.0)

    def _ranked(self, share, minimum: float) -> list[OurCountry]:
        shares: dict[OurCountry, float] = {}
        for row in self.shapes.itertuples():
            country = self._country(row)
            if country is None:
                continue
            value = share(row.geometry)
            if value >= minimum:
                shares[country] = shares.get(country, 0.0) + value
        return [country for country, _ in sorted(shares.items(), key=lambda item: (-item[1], item[0].name))]
