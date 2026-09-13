import itertools
import json
import logging
import pathlib
import time
from dataclasses import dataclass

import geopandas as gpd
import requests
from shapely.geometry import shape

from geography.data import DATA_DIR, Country, NaturalEarth
from geography.detail import AREA_TYPES
from geography.naming import slugify
from geography.sources.http import TIMEOUT, create_session

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
REQUEST_INTERVAL = 1.1
CACHE_DIR = DATA_DIR / "places"
LOOKUP_BATCH = 20
NOMINATIM_RETRIES = 2
POLYGON_THRESHOLD = 0.0005
CITY_ZOOM = 10
DISTRICT_ZOOM = 8

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Place:
    name: str
    geometry: gpd.GeoSeries
    country: Country
    approximate: bool = False


class Nominatim:
    def __init__(self, cache_dir=CACHE_DIR):
        self.cache_dir = cache_dir
        self.session = create_session(retries=NOMINATIM_RETRIES)
        self._last_request = 0.0

    def search(self, name: str, country_code: str) -> dict:
        cache_file = self.cache_dir / f"{slugify(f'{name}-{country_code}')}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text())

        feature = self._request(name, country_code)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(feature))
        return feature

    def relations(self, relation_ids: list[str]) -> dict[str, dict]:
        found, missing = {}, []
        for relation_id in relation_ids:
            cache_file = self._relation_cache(relation_id)
            if cache_file.exists():
                found[relation_id] = json.loads(cache_file.read_text())
            else:
                missing.append(relation_id)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for batch in itertools.batched(missing, LOOKUP_BATCH):
            for feature in self._lookup(list(batch)):
                relation_id = str(feature["properties"]["osm_id"])
                self._relation_cache(relation_id).write_text(json.dumps(feature))
                found[relation_id] = feature
        return found

    def _lookup(self, relation_ids: list[str]) -> list[dict]:
        try:
            return self._get("lookup", {"osm_ids": ",".join(f"R{relation_id}" for relation_id in relation_ids)})
        except requests.RequestException:
            if len(relation_ids) == 1:
                logger.warning("Skipping OSM relation %s", relation_ids[0])
                return []
            middle = len(relation_ids) // 2
            return self._lookup(relation_ids[:middle]) + self._lookup(relation_ids[middle:])

    def _relation_cache(self, relation_id: str) -> pathlib.Path:
        return self.cache_dir / f"osm-relation-{relation_id}.json"

    def reverse(self, latitude: float, longitude: float, zoom: int = CITY_ZOOM) -> dict | None:
        suffix = "" if zoom == CITY_ZOOM else f"-z{zoom}"
        cache_file = self.cache_dir / f"reverse-{latitude:.4f}-{longitude:.4f}{suffix}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text()) or None

        features = self._get("reverse", {"lat": latitude, "lon": longitude, "zoom": zoom})
        feature = features[0] if features else None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(feature))
        return feature

    def _request(self, name: str, country_code: str) -> dict:
        features = self._get("search", {"q": name, "countrycodes": country_code.lower(), "limit": 5})
        for feature in features:
            if feature["geometry"]["type"] in AREA_TYPES:
                return feature
        raise LookupError(f"No area found for {name} in {country_code}")

    def _get(self, endpoint: str, params: dict) -> list[dict]:
        time.sleep(max(0.0, self._last_request + REQUEST_INTERVAL - time.monotonic()))
        response = self.session.get(
            f"{NOMINATIM_URL}/{endpoint}",
            params={**params, "format": "geojson", "polygon_geojson": 1, "polygon_threshold": POLYGON_THRESHOLD},
            timeout=TIMEOUT,
        )
        self._last_request = time.monotonic()
        response.raise_for_status()
        return response.json()["features"]


class PlaceFinder:
    def __init__(self, data: NaturalEarth, nominatim: Nominatim | None = None):
        self.data = data
        self.nominatim = nominatim or Nominatim()

    def find(self, name: str, country: str) -> Place:
        host = self.data.find_country(country)
        feature = self.nominatim.search(name, host.iso_a2)
        geometry = gpd.GeoSeries([shape(feature["geometry"])], crs=self.data.crs)
        return Place(name, geometry, host)

