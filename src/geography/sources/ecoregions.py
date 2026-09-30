import logging
from datetime import timedelta

import geopandas as gpd

from geography.data import download_file, is_younger_than
from geography.detail import CACHE_DIR
from geography.render import EQUAL_AREA

ECOREGIONS_URL = "https://storage.googleapis.com/teow2016/Ecoregions2017.zip"
ECOREGIONS_FILE = CACHE_DIR / "Ecoregions2017.zip"
BLOCKS_FILE = CACHE_DIR / "rainforest_blocks.gpkg"
MONTANE = "montane"
MAX_AGE = timedelta(days=365)
MOIST_BROADLEAF_FOREST = 1
SIMPLIFY_M = 5_000
BRIDGE_M = 20_000
CRS = "EPSG:4326"

logger = logging.getLogger(__name__)


def rainforest_blocks(min_area_km2: float) -> gpd.GeoSeries:
    if not (BLOCKS_FILE.exists() and is_younger_than(BLOCKS_FILE, MAX_AGE)):
        build_blocks()
    blocks = gpd.read_file(BLOCKS_FILE).geometry
    return blocks[blocks.to_crs(EQUAL_AREA).area >= min_area_km2 * 1e6].reset_index(drop=True)


def build_blocks() -> None:
    path = download_file(ECOREGIONS_URL, ECOREGIONS_FILE, max_age=MAX_AGE)
    logger.info("Grouping rainforest ecoregions into blocks")
    ecoregions = gpd.read_file(f"zip://{path}", columns=["ECO_NAME", "BIOME_NUM"])
    lowland = ~ecoregions["ECO_NAME"].str.contains(MONTANE, case=False)
    forests = ecoregions[(ecoregions["BIOME_NUM"] == MOIST_BROADLEAF_FOREST) & lowland].to_crs(EQUAL_AREA)
    simplified = forests.geometry.simplify(SIMPLIFY_M).buffer(0)
    merged = simplified.buffer(BRIDGE_M).union_all()
    blocks = gpd.GeoSeries([merged], crs=EQUAL_AREA).explode(index_parts=False).buffer(-BRIDGE_M)
    gpd.GeoDataFrame(geometry=blocks.to_crs(CRS)).to_file(BLOCKS_FILE)
