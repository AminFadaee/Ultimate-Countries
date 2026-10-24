import json
import logging
import pathlib

import shapely
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge

from geography.detail import CACHE_DIR
from geography.sources.http import TIMEOUT

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE = CACHE_DIR / "overpass"
QUERY_TIMEOUT = 180
SIMPLIFY_DEGREES = 0.005
SNAP_DEGREES = 0.01
MIN_PART_DEGREES = 0.1
GAP_DEGREES = 0.4
ISOLATION_DEGREES = 3.0

logger = logging.getLogger(__name__)


def bridges(pieces: list[LineString]) -> list[LineString]:
    ends = [Point(end) for piece in pieces for end in (piece.coords[0], piece.coords[-1])]
    tree = STRtree(ends)
    connectors = []
    for index, end in enumerate(ends):
        others = [other for other in tree.query(end.buffer(GAP_DEGREES)) if other // 2 != index // 2]
        if others:
            nearest = min(others, key=lambda other: end.distance(ends[other]))
            connectors.append(LineString([end, ends[nearest]]))
    return connectors


def connected(parts) -> list[LineString]:
    parts = [part for part in parts if part.length >= MIN_PART_DEGREES]
    tree = STRtree(parts)
    return [part for index, part in enumerate(parts) if any(other != index for other in tree.query(part.buffer(ISOLATION_DEGREES)))]


class Overpass:
    def __init__(self, session, cache_dir: pathlib.Path = CACHE):
        self.session = session
        self.cache_dir = cache_dir

    def relation_types(self, relation_ids: list[str]) -> dict[str, str]:
        if not relation_ids:
            return {}
        elements = self._query(f"rel(id:{','.join(relation_ids)});out tags;")
        return {str(element["id"]): element.get("tags", {}).get("type", "") for element in elements}

    def line(self, relation_id: str) -> MultiLineString | None:
        cache_file = self.cache_dir / f"{relation_id}.json"
        if cache_file.exists():
            coordinates = json.loads(cache_file.read_text())
        else:
            elements = self._query(f"rel({relation_id});>>;way._;out geom;")
            ways = [[(point["lon"], point["lat"]) for point in element["geometry"]] for element in elements if element["type"] == "way" and element.get("geometry")]
            pieces = [LineString(way) for way in ways if len(way) > 1]
            snapped = shapely.set_precision(MultiLineString(pieces + bridges(pieces)), SNAP_DEGREES)
            line = linemerge(snapped).simplify(SIMPLIFY_DEGREES)
            coordinates = [list(part.coords) for part in connected(getattr(line, "geoms", [line]))]
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(coordinates))
        return MultiLineString(coordinates) if coordinates else None

    def _query(self, body: str) -> list[dict]:
        response = self.session.post(OVERPASS_URL, data={"data": f"[out:json][timeout:{QUERY_TIMEOUT}];{body}"}, timeout=TIMEOUT * 4)
        response.raise_for_status()
        return response.json()["elements"]
