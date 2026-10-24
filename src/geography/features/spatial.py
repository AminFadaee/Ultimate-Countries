import json
import pathlib
from dataclasses import dataclass

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, Point

from geography.data import CONTESTED_TYPES, NaturalEarth
from geography.render import EQUAL_AREA, get_mainland, get_nearby_parts

CRS = "EPSG:4326"
MIN_AREA_SHARE = 0.01
MIN_LINE_SHARE = 0.002
COAST_M = 10_000
LAKE_SHORE_M = 1_000
POINT_M = 3_000
CLAIM_REACH_M = 50_000
MIN_CONTAINED_SHARE = 0.5
MAX_LONGITUDE = 180
LINE_TYPES = ("LineString", "MultiLineString")
OUTLIER_FACTOR = 3


@dataclass(frozen=True)
class OurCountry:
    id: str
    alpha_3: str
    name: str
    continent: str | None
    wikipedia_url: str | None = None


def load_our_countries(countries_dir: pathlib.Path) -> dict[str, OurCountry]:
    found = {}
    for path in countries_dir.glob("*.json"):
        document = json.loads(path.read_text())
        if document["codes"]["alpha_3"]:
            alpha_3 = document["codes"]["alpha_3"]
            found[alpha_3] = OurCountry(document["id"], alpha_3, document["name"], document["region"], document["wikipedia_url"])
    return found


def split_antimeridian(geometry) -> MultiLineString:
    parts = []
    for line in getattr(geometry, "geoms", [geometry]):
        coords = [(min(max(x, -MAX_LONGITUDE), MAX_LONGITUDE), y) for x, y, *_ in line.coords]
        current = [coords[0]]
        for before, after in zip(coords[:-1], coords[1:], strict=True):
            if abs(after[0] - before[0]) > 180:
                parts.append(current)
                current = []
            current.append(after)
        parts.append(current)
    return MultiLineString([LineString(part) for part in parts if len(part) > 1])


def main_territory(geometry) -> object:
    parts = gpd.GeoSeries([geometry], crs=CRS)
    return get_nearby_parts(parts, get_mainland(parts.explode(index_parts=False))).union_all()


class CountryLocator:
    def __init__(self, data: NaturalEarth, ours: dict[str, OurCountry]):
        self.shapes = data.countries.to_crs(EQUAL_AREA)
        self.geodetic = data.countries
        self.ours = ours
        contested = data.disputed_areas[data.disputed_areas["TYPE"].isin(CONTESTED_TYPES)]
        self.contested = contested.union_all()

    def near(self, candidates: list[OurCountry], geometry: gpd.GeoSeries, reach_m: float) -> list[OurCountry]:
        unwrapped = gpd.GeoSeries([split_antimeridian(line) if line.geom_type in LINE_TYPES else line for line in geometry.geometry], crs=geometry.crs)
        shape = unwrapped.to_crs(EQUAL_AREA).union_all()
        found = {country for row in self.shapes.itertuples() if (country := self._country(row)) in candidates and row.geometry.distance(shape) <= reach_m}
        return sorted(found, key=lambda country: country.name)

    def is_contested(self, longitude: float, latitude: float) -> bool:
        return self.contested.contains(Point(longitude, latitude))

    def _country(self, row) -> OurCountry | None:
        return self.ours.get(row.ADM0_A3) or self.ours.get(row.ISO_A3)

    def countries_in(self, names: list[str], region_km2: float | None = None) -> gpd.GeoSeries:
        wanted = set(names)
        parts = gpd.GeoSeries(
            [main_territory(row.geometry) for row in self.geodetic.itertuples() if (country := self._country(row)) and country.name in wanted],
            crs=CRS,
        )
        areas = parts.to_crs(EQUAL_AREA).area
        typical = [area <= OUTLIER_FACTOR * (areas.sum() - area) or (region_km2 or 0) * 1e6 >= area for area in areas]
        return gpd.GeoSeries([parts[typical].union_all()], crs=CRS)

    def by_area(self, geometry: gpd.GeoSeries) -> list[OurCountry]:
        shape = geometry.to_crs(EQUAL_AREA).union_all()
        found = self._ranked(lambda country: country.intersection(shape).area / shape.area, MIN_AREA_SHARE)
        inside = self._ranked(lambda country: country.intersection(shape).area / country.area if country.area else 0.0, MIN_CONTAINED_SHARE)
        return found + [country for country in inside if country not in found]

    def by_line(self, geometry: gpd.GeoSeries) -> list[OurCountry]:
        split = gpd.GeoSeries([split_antimeridian(shape) for shape in geometry.geometry], crs=geometry.crs)
        line = split.to_crs(EQUAL_AREA).union_all()
        return self._ranked(lambda country: country.intersection(line).length / line.length, MIN_LINE_SHARE)

    def by_coast(self, geometry: gpd.GeoSeries, reach_m: float = COAST_M) -> list[OurCountry]:
        shape = geometry.to_crs(EQUAL_AREA).union_all().buffer(reach_m)
        return self._ranked(lambda country: 1.0 if country.intersects(shape) else 0.0, 1.0)

    def at_point(self, longitude: float, latitude: float, radius_m: float = POINT_M) -> list[OurCountry]:
        spot = gpd.GeoSeries([Point(longitude, latitude)], crs=CRS).to_crs(EQUAL_AREA)
        inside = self._ranked(lambda country: 1.0 if country.contains(spot.iloc[0]) else 0.0, 1.0)
        nearby = spot.buffer(radius_m).iloc[0]
        return inside or self._ranked(lambda country: 1.0 if country.intersects(nearby) else 0.0, 1.0)

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
