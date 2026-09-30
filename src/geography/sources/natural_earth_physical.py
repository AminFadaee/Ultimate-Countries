from datetime import timedelta
from enum import StrEnum

import geopandas as gpd

from geography.data import download_file
from geography.detail import CACHE_DIR

PHYSICAL_URL = "https://naciscdn.org/naturalearth/10m/physical"
MAX_AGE = timedelta(days=90)


class PhysicalLayer(StrEnum):
    REGIONS = "geography_regions_polys"
    MARINE = "geography_marine_polys"
    RIVERS = "rivers_lake_centerlines"
    LAKES = "lakes"
    ELEVATION_POINTS = "geography_regions_elevation_points"
    LINES = "geographic_lines"


def load_layer(layer: PhysicalLayer) -> gpd.GeoDataFrame:
    filename = f"ne_10m_{layer.value}.zip"
    path = download_file(f"{PHYSICAL_URL}/{filename}", CACHE_DIR / filename, max_age=MAX_AGE)
    frame = gpd.read_file(f"zip://{path}")
    frame.columns = [column.lower() if column != "geometry" else column for column in frame.columns]
    return frame
