import logging
from datetime import timedelta

import geopandas as gpd
import pandas as pd
import requests

from geography.data import is_younger_than
from geography.detail import CACHE_DIR
from geography.sources.http import TIMEOUT

WFS_URL = "https://geo.vliz.be/geoserver/MarineRegions/wfs"
IHO_LAYER = "MarineRegions:iho"
CACHE_FILE = CACHE_DIR / "iho_sea_areas.gpkg"
MAX_AGE = timedelta(days=180)
CRS = "EPSG:4326"

logger = logging.getLogger(__name__)


def request_features(session: requests.Session, **params: str) -> dict:
    response = session.get(
        WFS_URL,
        params={"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": IHO_LAYER,
                "outputFormat": "application/json", **params},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def fetch_sea_areas(session: requests.Session) -> gpd.GeoDataFrame:
    if CACHE_FILE.exists() and is_younger_than(CACHE_FILE, MAX_AGE):
        return gpd.read_file(CACHE_FILE)
    listing = request_features(session, propertyName="name,mrgid")
    frames = []
    for feature in listing["features"]:
        mrgid = feature["properties"]["mrgid"]
        logger.info("Downloading IHO sea area %s", feature["properties"]["name"])
        area = gpd.GeoDataFrame.from_features(request_features(session, CQL_FILTER=f"mrgid={mrgid}")["features"], crs=CRS)
        frames.append(area[["name", "mrgid", "geometry"]])
    areas = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=CRS)
    areas["mrgid"] = areas["mrgid"].astype(int).astype(str)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    areas.to_file(CACHE_FILE)
    return areas
