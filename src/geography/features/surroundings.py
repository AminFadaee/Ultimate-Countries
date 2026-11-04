import functools
from dataclasses import dataclass

import geopandas as gpd
from shapely.geometry import Point, shape

from geography.features.catalog import natural_earth_shapes
from geography.features.kinds import Kind
from geography.features.riverart.kinds import MARINE, RIVER_LIKE, WATER_BODY
from geography.features.spatial import CRS
from geography.places import Nominatim
from geography.sources.natural_earth_physical import PhysicalLayer, load_layer
from geography.sources.wikidata import POINT, Wikidata, qid_of, values_clause

MAX_RIVERS = 4
RIVER_REACH_DEG = 0.05
LINE_TYPES = ("LineString", "MultiLineString")
AREA_TYPES = ("Polygon", "MultiPolygon")
WATER_CLASSES = MARINE | WATER_BODY | {"gulf", "bay", "lagoon", "basin", "harbour", "port"}
LAKE_LINKS = ("P201", "P200")
CANAL_LINKS = ("P2789", "P403", "P885", "P206")
ON_RIVER_LINKS = ("P206",)
ON_A_RIVER = (Kind.WATERFALL, Kind.CANYON)


@dataclass(frozen=True)
class Waterway:
    name: str
    shape: gpd.GeoSeries | None
    point: Point | None = None


@dataclass(frozen=True)
class Surroundings:
    rivers: tuple[Waterway, ...] = ()
    waters: tuple[Waterway, ...] = ()
    landmark: Kind | None = None

    @property
    def facts(self) -> dict:
        return {"rivers": [river.name for river in self.rivers], "waters": [water.name for water in self.waters]}

    def __bool__(self) -> bool:
        return bool(self.rivers or self.waters)


@dataclass(frozen=True)
class Linked:
    qid: str
    name: str
    prop: str
    links: int
    point: Point | None
    classes: frozenset[str]

    @property
    def is_river(self) -> bool:
        return bool(self.classes & RIVER_LIKE) and not self.classes & WATER_CLASSES

    @property
    def is_water(self) -> bool:
        return bool(self.classes & WATER_CLASSES)


class SurroundingsResearch:
    def __init__(self, wikidata: Wikidata):
        self.wikidata = wikidata
        self.nominatim = Nominatim()

    def of(self, kind: Kind, qid: str, geometry: gpd.GeoSeries) -> Surroundings:
        if kind is Kind.LAKE:
            linked = self._linked(qid, LAKE_LINKS)
            ordered = sorted((item for item in linked if item.is_river), key=lambda item: (LAKE_LINKS.index(item.prop), -item.links))
            return Surroundings(rivers=self._drawn(ordered)[:MAX_RIVERS])
        if kind is Kind.CANAL:
            linked = self._linked(qid, CANAL_LINKS)
            waters = self._outlined(unique(item for item in linked if item.is_water))
            return Surroundings(rivers=self._drawn(item for item in linked if item.is_river)[:MAX_RIVERS], waters=waters)
        if kind in ON_A_RIVER:
            rivers = self._drawn(sorted((item for item in self._linked(qid, ON_RIVER_LINKS) if item.is_river), key=lambda item: -item.links))
            return Surroundings(rivers=rivers[:1] or self._nearest_river(geometry), landmark=kind)
        return Surroundings()

    @staticmethod
    @functools.cache
    def shapes() -> dict[str, gpd.GeoSeries]:
        return natural_earth_shapes()

    @staticmethod
    @functools.cache
    def rivers() -> gpd.GeoDataFrame:
        frame = load_layer(PhysicalLayer.RIVERS)
        return frame[frame["name"].notna()].to_crs(CRS)

    def _linked(self, qid: str, props: tuple[str, ...]) -> list[Linked]:
        rows = self.wikidata._select(f"""
            SELECT ?p ?v ?vl ?n ?c ?tl WHERE {{
              VALUES ?p {{ {" ".join(f"wdt:{prop}" for prop in props)} }}
              wd:{qid} ?p ?v . ?v rdfs:label ?vl FILTER(lang(?vl) = 'en')
              OPTIONAL {{ ?v wikibase:sitelinks ?n }}
              OPTIONAL {{ ?v wdt:P625 ?c }}
              OPTIONAL {{ ?v wdt:P31 ?t . ?t rdfs:label ?tl FILTER(lang(?tl) = 'en') }}
            }}""")
        found: dict[str, dict] = {}
        for row in rows:
            entry = found.setdefault(row["v"], {"row": row, "classes": set()})
            if "tl" in row:
                entry["classes"].add(row["tl"].lower())
        items = []
        for uri, entry in found.items():
            row = entry["row"]
            match = POINT.search(row.get("c", ""))
            point = Point(float(match.group(1)), float(match.group(2))) if match else None
            items.append(Linked(qid_of(uri), row["vl"], row["p"].rsplit("/", 1)[-1], int(row.get("n", 0)), point, frozenset(entry["classes"])))
        return sorted(items, key=lambda item: (props.index(item.prop), -item.links, item.name))

    def _drawn(self, items) -> tuple[Waterway, ...]:
        items = list(unique(items))
        traced = self._traced({item.qid for item in items if item.qid not in self.shapes()})
        drawn = []
        for item in items:
            lines = self.shapes().get(item.qid)
            if lines is None:
                lines = traced.get(item.qid)
            if lines is not None and not lines.empty and lines.geom_type.isin(LINE_TYPES).any():
                drawn.append(Waterway(item.name, lines[lines.geom_type.isin(LINE_TYPES)]))
        return tuple(drawn)

    def _outlined(self, items) -> tuple[Waterway, ...]:
        items = list(items)
        traced = self._traced({item.qid for item in items if item.qid not in self.shapes()})
        outlined = []
        for item in items:
            outline = self.shapes().get(item.qid)
            if outline is None:
                outline = traced.get(item.qid)
            areas = [] if outline is None else [shape for shape in outline.geometry if shape.geom_type in AREA_TYPES]
            areas += [shape for part in self._parts(item.qid) if (found := self.shapes().get(part)) is not None
                      for shape in found.geometry if shape.geom_type in AREA_TYPES]
            if areas:
                outlined.append(Waterway(item.name, gpd.GeoSeries(areas, crs=CRS), item.point))
            elif item.point is not None:
                outlined.append(Waterway(item.name, None, item.point))
        return tuple(outlined)

    def _parts(self, qid: str) -> list[str]:
        rows = self.wikidata._select(f"SELECT DISTINCT ?part WHERE {{ {{ ?part wdt:P361+ wd:{qid} }} UNION {{ wd:{qid} wdt:P527 ?part }} }}")
        return [qid_of(row["part"]) for row in rows]

    def _traced(self, qids: set[str]) -> dict[str, gpd.GeoSeries]:
        if not qids:
            return {}
        rows = self.wikidata._select(f"SELECT ?c ?osm WHERE {{ VALUES ?c {{ {values_clause(qids)} }} ?c wdt:P402 ?osm . }}")
        relations = {qid_of(row["c"]): row["osm"] for row in rows}
        surveyed = self.nominatim.relations(sorted(set(relations.values()))) if relations else {}
        return {qid: gpd.GeoSeries([shape(surveyed[osm]["geometry"])], crs=CRS) for qid, osm in relations.items() if osm in surveyed}

    def _nearest_river(self, geometry: gpd.GeoSeries) -> tuple[Waterway, ...]:
        spot = geometry.union_all().representative_point()
        rivers = self.rivers()
        nearby = rivers[rivers.distance(spot) <= RIVER_REACH_DEG]
        if nearby.empty:
            return ()
        nearest = nearby.loc[nearby.distance(spot).idxmin()]
        name = nearest["name_en"] if isinstance(nearest["name_en"], str) else nearest["name"]
        lines = rivers[(rivers["name_en"] == name) | (rivers["name"] == nearest["name"])].geometry
        return (Waterway(name, gpd.GeoSeries(list(lines), crs=CRS)),)


def unique(items):
    seen = set()
    for item in items:
        if item.qid not in seen:
            seen.add(item.qid)
            yield item
