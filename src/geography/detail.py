import logging
import pathlib
from dataclasses import dataclass
from datetime import timedelta

import geopandas as gpd
import shapely
from shapely.geometry import shape

from geography.data import DATA_DIR, download_file

LAND_URL = "https://osmdata.openstreetmap.de/download/simplified-land-polygons-complete-3857.zip"
LAND_ARCHIVE = "simplified-land-polygons-complete-3857.zip"
LAND_LAYER = "simplified-land-polygons-complete-3857/simplified_land_polygons.shp"
LAND_MAX_AGE = timedelta(days=30)
CACHE_DIR = DATA_DIR / "cache"
COUNTRIES_FILE = CACHE_DIR / "detail_countries.gpkg"
LAKES_FILE = CACHE_DIR / "detail_lakes.gpkg"
MIN_LAKE_AREA = 50_000_000
TOLERANCE = 0.002
TOLERANCE_SHARE = 0.02
CRS = "EPSG:4326"
AREA_TYPES = ("Polygon", "MultiPolygon")

logger = logging.getLogger(__name__)


def tolerance_for(geometry) -> float:
    minx, miny, maxx, maxy = geometry.bounds
    return min(TOLERANCE, max(maxx - minx, maxy - miny) * TOLERANCE_SHARE)


def simplify(geometry):
    return shapely.make_valid(geometry.simplify(tolerance_for(geometry), preserve_topology=True))


def load_land() -> gpd.GeoSeries:
    archive = download_file(LAND_URL, CACHE_DIR / LAND_ARCHIVE, LAND_MAX_AGE)
    land = gpd.read_file(f"/vsizip/{archive}/{LAND_LAYER}").geometry
    return land.to_crs(CRS)


def clip_to_land(geometry, land: gpd.GeoSeries):
    candidates = land.iloc[land.sindex.query(geometry, predicate="intersects")]
    if candidates.empty:
        return None
    clipped = shapely.union_all(candidates.intersection(geometry).values)
    return None if clipped.is_empty else simplify(clipped)


@dataclass
class DetailLayers:
    land: gpd.GeoSeries
    countries: gpd.GeoSeries
    lakes: gpd.GeoSeries

    @classmethod
    def load(cls) -> "DetailLayers | None":
        if not COUNTRIES_FILE.exists() or not LAKES_FILE.exists():
            return None
        countries = gpd.read_file(COUNTRIES_FILE).set_index("id").geometry
        return cls(load_land(), countries, gpd.read_file(LAKES_FILE).geometry)

    def country(self, country_id: str):
        return self.countries.get(country_id)

    def land_in(self, view) -> gpd.GeoSeries:
        return self._clipped(self.land, view)

    def borders_in(self, view) -> gpd.GeoSeries:
        return self._clipped(self.countries, view)

    def lakes_in(self, view) -> gpd.GeoSeries:
        return self._clipped(self.lakes, view)

    def _clipped(self, layer: gpd.GeoSeries, view) -> gpd.GeoSeries:
        window = shapely.box(*view.bounds)
        found = layer.iloc[layer.sindex.query(window, predicate="intersects")]
        return found.intersection(window)


def build_country_layer(boundaries: dict[str, dict], fallbacks: dict, path: pathlib.Path = COUNTRIES_FILE) -> int:
    land = load_land()
    ids, geometries = [], []
    for country_id in sorted(boundaries.keys() | fallbacks.keys()):
        feature = boundaries.get(country_id)
        if feature and feature["geometry"]["type"] in AREA_TYPES:
            outline = shape(feature["geometry"])
        elif country_id in fallbacks:
            outline = fallbacks[country_id]
        else:
            continue
        clipped = clip_to_land(shapely.make_valid(outline), land)
        if clipped is None:
            logger.warning("No land inside boundary of %s", country_id)
            continue
        ids.append(country_id)
        geometries.append(clipped)

    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame({"id": ids}, geometry=geometries, crs=CRS).to_file(path, driver="GPKG")
    return len(ids)


def build_lake_layer(features: dict[str, dict], path: pathlib.Path = LAKES_FILE) -> int:
    geometries = [
        simplify(shapely.make_valid(shape(feature["geometry"])))
        for feature in features.values()
        if feature["geometry"]["type"] in AREA_TYPES
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(geometry=geometries, crs=CRS).to_file(path, driver="GPKG")
    return len(geometries)
