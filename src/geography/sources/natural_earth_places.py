from datetime import timedelta

import geopandas as gpd

from geography.data import download_file
from geography.detail import CACHE_DIR

PLACES_URL = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_populated_places.zip"
PLACES_FILE = CACHE_DIR / "ne_10m_populated_places.zip"
MAX_AGE = timedelta(days=90)


def load_populated_places() -> gpd.GeoDataFrame:
    path = download_file(PLACES_URL, PLACES_FILE, max_age=MAX_AGE)
    frame = gpd.read_file(f"zip://{path}")
    frame.columns = [column.lower() if column != "geometry" else column for column in frame.columns]
    return frame
