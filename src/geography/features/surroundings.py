import functools
from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
import shapely
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
PEAKS = (Kind.MOUNTAIN, Kind.VOLCANO)
HIGHEST_POINT = (Kind.MOUNTAIN_RANGE, Kind.PLATEAU, Kind.DESERT, Kind.PENINSULA, Kind.BASIN)
SHORES = (Kind.PENINSULA, Kind.ISTHMUS, Kind.ISLAND, Kind.REEF)
SEAS = (Kind.SEA,)
CROSSED = (Kind.DELTA, Kind.BASIN, Kind.VALLEY, Kind.PLAIN, Kind.RAINFOREST, Kind.WETLAND)
TEXTURED = (Kind.DESERT, Kind.RAINFOREST, Kind.WETLAND)
SURROUNDED = (Kind.LAKE, Kind.CANAL, *ON_A_RIVER, *PEAKS, *HIGHEST_POINT, *SHORES, *SEAS, *CROSSED, Kind.LANDMARK, Kind.PARK, Kind.POLDER)
SEA_LINKS = ("P2789", "P47")
SHORE_LINKS = ("P206",)
SETTLEMENT_WORDS = ("city", "town", "municipality", "village", "commune", "capital")
MAX_CROSSING = 2
MAX_WATERS = 4
MIN_WATER_LINKS = 20
TOUCHING_DEG = 0.02
OWN_SHARE = 0.5
RANGE_REACH_DEG = 0.5
OPEN_WATER = ("ocean", "sea", "gulf", "bay", "strait", "channel", "sound", "fjord", "inlet")
MAX_LOCATION_STEPS = 4


@dataclass(frozen=True)
class Waterway:
    name: str
    shape: gpd.GeoSeries | None
    point: Point | None = None


@dataclass(frozen=True)
class Marker:
    name: str
    point: Point


@dataclass(frozen=True)
class Surroundings:
    rivers: tuple[Waterway, ...] = ()
    waters: tuple[Waterway, ...] = ()
    landmark: Kind | None = None
    ranges: tuple[Waterway, ...] = ()
    peaks: tuple[Marker, ...] = ()
    towns: tuple[Marker, ...] = ()
    texture: Kind | None = None
    rivers_inside: bool = False

    @property
    def facts(self) -> dict:
        found = {"rivers": [river.name for river in self.rivers], "waters": [water.name for water in self.waters]}
        extra = (("ranges", self.ranges), ("peaks", self.peaks), ("towns", self.towns))
        return found | {name: [item.name for item in items] for name, items in extra if items}

    def __bool__(self) -> bool:
        return bool(self.rivers or self.waters or self.ranges or self.peaks or self.towns or self.texture or self.landmark)


@dataclass(frozen=True)
class Linked:
    qid: str
    name: str
    prop: str
    links: int
    point: Point | None
    classes: frozenset[str]
    points: tuple[Point, ...] = ()

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
        if kind in PEAKS:
            return Surroundings(ranges=self._range(qid, geometry), landmark=kind)
        if kind in SEAS:
            own = {qid, *self._parts(qid)}
            connected = (item for item in self._linked(qid, SEA_LINKS) if item.is_water and item.qid not in own)
            return Surroundings(waters=self._outlined(connected, own)[:MAX_WATERS] or self._touching_waters(geometry, own, marine=True))
        if kind is Kind.POLDER:
            return Surroundings(waters=self._touching_waters(geometry))
        if kind is Kind.LANDMARK:
            return Surroundings(rivers=self._drawn(item for item in self._linked(qid, ON_RIVER_LINKS) if item.is_river)[:1])
        if kind is Kind.PARK:
            return Surroundings(towns=self._towns(qid))
        found = {}
        if kind in HIGHEST_POINT:
            found["peaks"] = self._highest(qid, geometry)
        if kind in SHORES:
            known = (item for item in self._linked(qid, SHORE_LINKS) if item.is_water and item.links >= MIN_WATER_LINKS)
            found["waters"] = self._outlined(known)[:MAX_WATERS] or self._touching_waters(geometry)
        if kind in CROSSED:
            linked = self._drawn(item for item in self._linked(qid, ON_RIVER_LINKS) if item.is_river)
            found["rivers"] = linked[:MAX_CROSSING] or self._crossing(geometry)
            found["rivers_inside"] = True
        if kind in TEXTURED:
            found["texture"] = kind
        return Surroundings(**found)

    def _highest(self, qid: str, geometry: gpd.GeoSeries) -> tuple[Marker, ...]:
        area = geometry.union_all()
        return tuple(Marker(item.name, min(item.points, key=area.distance)) for item in self._linked(qid, ("P610",)) if item.points)[:1]

    def _range(self, qid: str, geometry: gpd.GeoSeries) -> tuple[Waterway, ...]:
        named = self._linked(qid, ("P4552",))
        spot = geometry.union_all().representative_point()
        if outlined := [item for item in named if item.qid in self.shapes() and shapely.distance(self.shapes()[item.qid].union_all(), spot) <= RANGE_REACH_DEG]:
            return (Waterway(outlined[0].name, self.shapes()[outlined[0].qid]),)
        ranges = self.regions()
        holding = ranges[(ranges["featurecla"] == "Range/mtn") & ranges.contains(spot)]
        if named:
            holding = holding[holding["wikidataid"].isin(self._ranges_above({item.qid for item in named}))]
        if holding.empty:
            return ()
        best = holding.iloc[int(shapely.area(holding.geometry.values).argmin())]
        return (Waterway(english(best), gpd.GeoSeries([best.geometry], crs=CRS)),)

    def _ranges_above(self, qids: set[str]) -> set[str]:
        rows = self.wikidata._select(f"SELECT DISTINCT ?above WHERE {{ VALUES ?range {{ {values_clause(qids)} }} ?range (wdt:P4552|wdt:P361)* ?above . }}")
        return {qid_of(row["above"]) for row in rows}

    def _crossing(self, geometry: gpd.GeoSeries) -> tuple[Waterway, ...]:
        area = geometry.union_all()
        rivers = self.rivers()
        inside = rivers[rivers.intersects(area)]
        if inside.empty:
            return ()
        named = inside.assign(english=inside.apply(english, axis=1), inside=shapely.length(inside.geometry.intersection(area).values))
        longest = named.groupby("english")["inside"].sum().sort_values(ascending=False).index[:MAX_CROSSING]
        every = rivers.apply(english, axis=1)
        return tuple(Waterway(name, gpd.GeoSeries(list(rivers[every == name].geometry), crs=CRS)) for name in longest)

    def _towns(self, qid: str) -> tuple[Marker, ...]:
        level = [qid]
        for _ in range(MAX_LOCATION_STEPS):
            above = [item for current in level for item in self._linked(current, ("P131",))]
            settled = [item for item in above if item.point is not None and any(word in name for name in item.classes for word in SETTLEMENT_WORDS)]
            if settled:
                best = max(settled, key=lambda item: item.links)
                return (Marker(best.name, best.point),)
            level = [item.qid for item in above]
        return ()

    def _touching_waters(self, geometry: gpd.GeoSeries, own: set[str] = frozenset(), marine: bool = False) -> tuple[Waterway, ...]:
        shape = geometry.union_all()
        waters = self.waters()
        if marine:
            waters = waters[waters["featurecla"].isin(OPEN_WATER)]
        touching = waters[waters.intersects(shape.buffer(TOUCHING_DEG)) & ~waters["wikidataid"].isin(own)]
        touching = touching[shapely.area(touching.geometry.intersection(shape).values) < OWN_SHARE * shapely.area(touching.geometry.values)]
        touching = touching.sort_values("scalerank", kind="stable")
        return tuple(Waterway(english(row), gpd.GeoSeries([row.geometry], crs=CRS)) for _, row in touching.iterrows())[:MAX_WATERS]

    @staticmethod
    @functools.cache
    def regions() -> gpd.GeoDataFrame:
        return load_layer(PhysicalLayer.REGIONS).to_crs(CRS)

    @staticmethod
    @functools.cache
    def waters() -> gpd.GeoDataFrame:
        frames = [load_layer(layer).to_crs(CRS) for layer in (PhysicalLayer.LAKES, PhysicalLayer.MARINE)]
        frame = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=CRS)
        return frame[frame["name"].notna()]

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
              wd:{qid} ?p ?v .
              OPTIONAL {{ ?v rdfs:label ?en FILTER(lang(?en) = 'en') }}
              OPTIONAL {{ ?v rdfs:label ?mul FILTER(lang(?mul) = 'mul') }}
              BIND(COALESCE(?en, ?mul) AS ?vl) FILTER(BOUND(?vl))
              OPTIONAL {{ ?v wikibase:sitelinks ?n }}
              OPTIONAL {{ ?v wdt:P625 ?c }}
              OPTIONAL {{ ?v wdt:P31 ?t . ?t rdfs:label ?tl FILTER(lang(?tl) = 'en') }}
            }}""")
        found: dict[str, dict] = {}
        for row in rows:
            entry = found.setdefault(row["v"], {"row": row, "classes": set(), "points": {}})
            if "tl" in row:
                entry["classes"].add(row["tl"].lower())
            if match := POINT.search(row.get("c", "")):
                entry["points"].setdefault(row["c"], Point(float(match.group(1)), float(match.group(2))))
        items = []
        for uri, entry in found.items():
            row = entry["row"]
            points = tuple(entry["points"].values())
            items.append(Linked(qid_of(uri), row["vl"], row["p"].rsplit("/", 1)[-1], int(row.get("n", 0)), points[0] if points else None,
                                frozenset(entry["classes"]), points))
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

    def _outlined(self, items, excluded: set[str] = frozenset()) -> tuple[Waterway, ...]:
        items = list(items)
        traced = self._traced({item.qid for item in items if item.qid not in self.shapes()})
        outlined = []
        for item in items:
            outline = self.shapes().get(item.qid)
            if outline is None:
                outline = traced.get(item.qid)
            areas = [] if outline is None else [shape for shape in outline.geometry if shape.geom_type in AREA_TYPES]
            areas += [shape for part in self._parts(item.qid) if part not in excluded and (found := self.shapes().get(part)) is not None
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
        degrees = pd.Series(shapely.distance(rivers.geometry.values, spot), index=rivers.index)
        if degrees.min() > RIVER_REACH_DEG:
            return ()
        nearest = rivers.loc[degrees.idxmin()]
        name = nearest["name_en"] if isinstance(nearest["name_en"], str) else nearest["name"]
        lines = rivers[(rivers["name_en"] == name) | (rivers["name"] == nearest["name"])].geometry
        return (Waterway(name, gpd.GeoSeries(list(lines), crs=CRS)),)


def english(row) -> str:
    name = row.get("name_en") if isinstance(row.get("name_en"), str) else row["name"]
    return " ".join(word.capitalize() if word.isupper() else word for word in str(name).split())


def unique(items):
    seen = set()
    for item in items:
        if item.qid not in seen:
            seen.add(item.qid)
            yield item
