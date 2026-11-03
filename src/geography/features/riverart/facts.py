import logging
from dataclasses import asdict, dataclass, field

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import LineString, MultiPoint, Point, box, shape as to_shape

from geography.features.catalog import GEOD, article_title, drawn_km, natural_earth_shapes
from geography.features.model import Feature
from geography.features.riverart import geometry as river_geometry
from geography.features.riverart.infobox import InfoboxLinks, RiverInfobox
from geography.features.riverart.kinds import (
    AREA_WORDS, DRIED_WORDS, HIGHLAND, MARINE, RIVER_LIKE, WATER_BODY, WETTED_LAND, MouthKind, SourceKind, source_kind, source_label,
)
from geography.features.spatial import CRS
from geography.places import Nominatim
from geography.sources.wikidata import POINT, Wikidata, qid_of

MIN_LINKS = 100
REACH = 0.5
MAX_TRIBUTARIES = 5
MAX_PER_KIND = 2
WINDOW_MARGIN = 0.5
MIN_SINK_SHARE = 0.6
FADING_SHARE = 0.8
OSM_GAIN = 1.1
LAKE_QUERY_LIMIT = 6
OVERSIZED_AREA = 1.2
SOURCE_AGREEMENT_KM = 100
LAKE, WATERFALL, DAM, CANYON, GORGE, RIVER = "Q23397", "Q34038", "Q12323", "Q150784", "Q1668451", "Q4022"
AREAS = ("Polygon", "MultiPolygon")
LINES = ("LineString", "MultiLineString")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Landmark:
    name: str
    qid: str
    point: tuple[float, float] | None = None


@dataclass(frozen=True)
class Source:
    name: str
    qid: str
    kind: SourceKind
    classes: tuple[str, ...]
    point: tuple[float, float] | None = None


@dataclass(frozen=True)
class Mouth:
    name: str
    qid: str
    kind: MouthKind
    classes: tuple[str, ...]
    point: tuple[float, float] | None = None


@dataclass
class Sink:
    outline: gpd.GeoSeries | None
    whole: gpd.GeoSeries | None
    fades: bool


@dataclass
class PathLake:
    landmark: Landmark
    outline: gpd.GeoSeries | None


@dataclass
class RiverArt:
    river: gpd.GeoSeries
    path: LineString
    upstream: Point
    mouth_point: Point
    source: Source | None = None
    source_area: gpd.GeoSeries | None = None
    source_lake: gpd.GeoSeries | None = None
    mouth: Mouth | None = None
    sink: Sink | None = None
    tributaries: dict[str, gpd.GeoSeries] = field(default_factory=dict)
    linked: dict[str, gpd.GeoSeries] = field(default_factory=dict)
    source_rivers: tuple[str, ...] = ()
    lakes: tuple[PathLake, ...] = ()
    falls: tuple[Landmark, ...] = ()
    dams: tuple[Landmark, ...] = ()
    gorges: tuple[Landmark, ...] = ()

    @property
    def source_point(self) -> Point:
        point = as_point(self.source.point) if self.source else None
        return point if point is not None else self.upstream

    @property
    def facts(self) -> dict:
        return {
            "source": asdict(self.source) if self.source else None,
            "mouth": asdict(self.mouth) if self.mouth else None,
            "linked_rivers": sorted(self.linked),
            "tributaries": sorted(self.tributaries),
            "lakes": [asdict(lake.landmark) for lake in self.lakes],
            "waterfalls": [asdict(item) for item in self.falls],
            "dams": [asdict(item) for item in self.dams],
            "gorges": [asdict(item) for item in self.gorges],
        }


def point_of(text: str | None) -> tuple[float, float] | None:
    match = POINT.search(text or "")
    return (float(match.group(1)), float(match.group(2))) if match else None


def as_point(coordinates: tuple[float, float] | None) -> Point | None:
    return Point(coordinates) if coordinates else None


def is_area(shape: gpd.GeoSeries | None) -> bool:
    return shape is not None and shape.geom_type.isin(AREAS).all()


class RiverResearch:
    def __init__(self, session: requests.Session, wikidata: Wikidata, language: str | None = None):
        self.wikidata = wikidata
        self.infobox = RiverInfobox(session)
        self.language = language if language != "en" else None
        self.native = RiverInfobox(session, self.language) if self.language else None
        self.nominatim = Nominatim()
        self.shapes = natural_earth_shapes()
        self._landmarks: dict[str, list[tuple[Landmark, Point]]] = {}

    def art(self, feature: Feature) -> RiverArt | None:
        river = self._drawn_river(feature)
        if river is None:
            return None
        natural = river_geometry_of(feature.geometry)
        links = self.infobox.links(article_title(feature.wikipedia)) if feature.wikipedia else InfoboxLinks((), ())
        candidates = self._sources(feature.qid, links) or self._native_sources(feature.qid, links)
        origin = self._origin(candidates)
        mouth_row, dried = self._mouth_row(feature.qid, list(links.mouth))
        mouth_classes = self._classes(mouth_row["m"]) if mouth_row else set()
        mouth_is_river = bool(mouth_row) and self._is_a(mouth_row["m"], RIVER)
        window = self._window(river)
        tributaries = self._tributaries(feature.qid, window)
        linked, source_rivers = self._linked_rivers(mouth_row if mouth_is_river else None, [c for c in candidates if c["classes"] & RIVER_LIKE], window)
        sink = None if mouth_is_river or not mouth_row or mouth_classes & MARINE else self._sink(mouth_row["m"], river, window)
        source_area, source_lake, kind = self._source_terrain(origin)
        mouth_river = linked.get(mouth_row["ml"]) if mouth_is_river else None
        upstream, mouth_point = self._ends(river, origin, source_area, source_lake, mouth_row, mouth_classes, mouth_is_river, mouth_river, tributaries)
        main = river_geometry.main_pieces(river)
        path = river_geometry.ordered_path(main, upstream, mouth_point)
        river = river_geometry.without_side_channels(river, path)
        if natural is not None:
            path, added = river_geometry.extended(path, natural)
            river = gpd.GeoSeries([*river, *added], crs=CRS)
            if added:
                mouth_point = Point(path.coords[-1])
        if mouth_river is not None and (trimmed := river_geometry.trimmed_at_confluence(river, path, mouth_river)):
            river, path = trimmed
            mouth_point = Point(path.coords[-1])
        if kind is SourceKind.LAKE:
            exit_index = river_geometry.lake_exit(path, source_lake, as_point(point_of(origin["c"])) if origin else None)
            if trimmed := river_geometry.trimmed_at_lake(river, path, exit_index):
                river, path = trimmed
                upstream = Point(path.coords[0])
        if source_area is not None and kind is SourceKind.HIGHLAND and (self._far_from(upstream, source_area, river) or oversized(source_area, river)):
            source_area = None
        if kind is not SourceKind.HIGHLAND:
            source_area = None
        return RiverArt(
            river=river,
            path=path,
            upstream=upstream,
            mouth_point=mouth_point,
            source=self._source(origin, kind, upstream),
            source_area=source_area,
            source_lake=source_lake,
            mouth=self._mouth(mouth_row, mouth_classes, mouth_is_river, dried, sink),
            sink=sink,
            tributaries=tributaries,
            linked=linked,
            source_rivers=source_rivers,
            lakes=self._path_lakes(feature.qid, river, origin),
            falls=self._along(river, WATERFALL),
            dams=self._along(river, DAM),
            gorges=self._gorges(river),
        )

    def _drawn_river(self, feature: Feature) -> gpd.GeoSeries | None:
        river = river_geometry_of(feature.geometry)
        if river is None:
            return None
        for part in self._rows(f"SELECT DISTINCT ?p WHERE {{ {{ wd:{feature.qid} wdt:P527 ?p }} UNION {{ ?p wdt:P361 wd:{feature.qid} }} ?p wdt:P31/wdt:P279* wd:{RIVER} }}"):
            if (listed := self.shape(qid_of(part["p"]), LINES)) is not None:
                river = gpd.GeoSeries(pd.concat([river, listed]).values, crs=CRS)
        if (traced := self._openstreetmap(feature.qid)) is not None and drawn_km(traced) > OSM_GAIN * drawn_km(river):
            river = traced
        return river

    def shape(self, qid: str, kinds: tuple[str, ...]) -> gpd.GeoSeries | None:
        if qid in self.shapes:
            geometry = self.shapes[qid]
        elif (geometry := self._openstreetmap(qid)) is None:
            return None
        parts = [part for part in geometry.make_valid().explode(index_parts=False) if part.geom_type in kinds and not part.is_empty]
        return gpd.GeoSeries(parts, crs=CRS) if parts else None

    def _openstreetmap(self, qid: str) -> gpd.GeoSeries | None:
        relations = self.wikidata.osm_relations({qid})
        found = self.nominatim.relations(sorted(set(relations.values()))).get(relations.get(qid, ""))
        return gpd.GeoSeries([to_shape(found["geometry"])], crs=CRS) if found else None

    def _rows(self, query: str) -> list[dict[str, str]]:
        return self.wikidata._select(query)

    def _classes(self, qid_uri: str) -> set[str]:
        rows = self._rows(f"SELECT ?cl WHERE {{ wd:{qid_of(qid_uri)} wdt:P31 ?t . ?t rdfs:label ?cl FILTER(lang(?cl)='en') }}")
        return {row["cl"].lower() for row in rows}

    def _is_a(self, qid_uri: str, class_qid: str) -> bool:
        return bool(self._rows(f"SELECT ?x WHERE {{ wd:{qid_of(qid_uri)} wdt:P31/wdt:P279* wd:{class_qid} BIND(1 AS ?x) }}"))

    def _sources(self, river_qid: str, links: InfoboxLinks) -> list[dict]:
        select = "SELECT ?s ?sl ?c ?tl ?n WHERE {{ {anchor} ?s rdfs:label ?sl FILTER(lang(?sl)='en') OPTIONAL {{ ?s wdt:P625 ?c }} OPTIONAL {{ ?s wikibase:sitelinks ?n }} OPTIONAL {{ ?s wdt:P31 ?t . ?t rdfs:label ?tl FILTER(lang(?tl)='en') }} }}"
        rows = self._rows(select.format(anchor=f"wd:{river_qid} wdt:P885 ?s ."))
        listed = [item for source in links.sources for item in source.items]
        if not rows and listed:
            rows = self._rows(select.format(anchor=f"VALUES ?s {{ {' '.join('wd:' + qid for qid in listed)} }}"))
        found: dict[str, dict] = {}
        for row in rows:
            item = found.setdefault(row["s"], {"uri": row["s"], "qid": qid_of(row["s"]), "label": row["sl"], "c": row.get("c"), "classes": set(), "links": int(row.get("n", 0))})
            if "tl" in row:
                item["classes"].add(row["tl"].lower())
        if not found and not self.native:
            return self._written_sources(links)
        return list(found.values())

    def _native_sources(self, river_qid: str, links: InfoboxLinks) -> list[dict]:
        if not self.native:
            return []
        rows = self._rows(f"SELECT ?t WHERE {{ ?a schema:about wd:{river_qid} ; schema:isPartOf <https://{self.language}.wikipedia.org/> ; schema:name ?t }}")
        native = self.native.links(rows[0]["t"]) if rows else InfoboxLinks((), ())
        found = [item for item in self._sources(river_qid, native) if self._agrees(item, links)]
        return found or self._written_sources(links)

    @staticmethod
    def _agrees(item: dict, links: InfoboxLinks) -> bool:
        stated = next((as_point(source.point) for source in links.sources if source.point), None)
        found = as_point(point_of(item["c"]))
        if stated is None or found is None:
            return True
        return GEOD.inv(stated.x, stated.y, found.x, found.y)[2] / 1000 <= SOURCE_AGREEMENT_KM

    def _written_sources(self, links: InfoboxLinks) -> list[dict]:
        return [self._written(source) for source in links.sources if source.names and source.point][:1]

    def _written(self, source) -> dict:
        x, y = source.point
        return {"uri": None, "qid": "", "label": " / ".join(source.names), "c": f"Point({x} {y})", "classes": set(), "links": 0}

    def _origin(self, candidates: list[dict]) -> dict | None:
        def rank(item: dict):
            return (not is_area(self.shapes.get(item["qid"])), not item["classes"] & HIGHLAND, -item["links"], item["label"])

        origin = min(candidates, key=rank) if candidates else None
        return None if origin and origin["classes"] & RIVER_LIKE else origin

    def _mouth_row(self, river_qid: str, infobox_mouths: list[str]) -> tuple[dict | None, bool]:
        rows = self._rows(f"SELECT ?m ?ml WHERE {{ wd:{river_qid} wdt:P403 ?m . ?m rdfs:label ?ml FILTER(lang(?ml)='en') }}")
        if not rows:
            return None, False
        first = rows[0]
        classes = self._classes(first["m"])
        ended = bool(self._rows(f"SELECT ?e WHERE {{ wd:{qid_of(first['m'])} wdt:P576|wdt:P582 ?e }}"))
        dried = ended or any(word in name for name in classes for word in DRIED_WORDS)
        if not classes & MARINE and any(word in name for name in classes for word in AREA_WORDS) and infobox_mouths:
            replacement = self._rows(f"SELECT ?m ?ml WHERE {{ VALUES ?m {{ wd:{infobox_mouths[0]} }} ?m rdfs:label ?ml FILTER(lang(?ml)='en') }}")
            if replacement:
                first = replacement[0]
        return first, dried

    def _window(self, river: gpd.GeoSeries):
        x0, y0, x1, y1 = river.total_bounds
        margin = WINDOW_MARGIN * max(x1 - x0, y1 - y0)
        return box(x0 - margin, y0 - margin, x1 + margin, y1 + margin)

    def _clipped(self, qid: str, window) -> gpd.GeoSeries | None:
        lines = self.shape(qid, LINES)
        if lines is None:
            return None
        kept = [clip for line in lines.explode(index_parts=False) if not (clip := line.intersection(window)).is_empty]
        return gpd.GeoSeries(kept, crs=CRS) if kept else None

    def _tributaries(self, river_qid: str, window) -> dict[str, gpd.GeoSeries]:
        rows = self._rows(f"SELECT ?t ?tl ?len WHERE {{ wd:{river_qid} wdt:P974 ?t . ?t rdfs:label ?tl FILTER(lang(?tl)='en') OPTIONAL {{ ?t wdt:P2043 ?len }} }}")
        longest = sorted(rows, key=lambda row: -float(row.get("len", 0)))[:MAX_TRIBUTARIES]
        found = {}
        for row in longest:
            lines = self.shape(qid_of(row["t"]), LINES)
            if lines is None:
                continue
            inside = [line for line in lines.explode(index_parts=False) if line.intersects(window)]
            if inside:
                found[row["tl"]] = gpd.GeoSeries(inside, crs=CRS)
        return found

    def _linked_rivers(self, mouth_row: dict | None, source_rivers: list[dict], window) -> tuple[dict[str, gpd.GeoSeries], tuple[str, ...]]:
        found, sources = {}, []
        if mouth_row and (lines := self._clipped(qid_of(mouth_row["m"]), window)) is not None:
            found[mouth_row["ml"]] = lines
        for item in source_rivers:
            if (lines := self._clipped(item["qid"], window)) is not None:
                found[item["label"]] = lines
                sources.append(item["label"])
        return found, tuple(sources)

    def _sink(self, mouth_uri: str, river: gpd.GeoSeries, window) -> Sink:
        whole = self.shape(qid_of(mouth_uri), AREAS)
        inside = river.union_all().intersection(whole.union_all()).length / (river.union_all().length or 1) if whole is not None else 0.0
        fades = inside >= FADING_SHARE
        outline = None
        if whole is not None and not fades:
            clipped = whole.intersection(window)
            if not clipped.union_all().is_empty and clipped.union_all().area >= MIN_SINK_SHARE * whole.union_all().area:
                outline = clipped
        return Sink(outline, whole, fades)

    def _source_terrain(self, origin: dict | None) -> tuple[gpd.GeoSeries | None, gpd.GeoSeries | None, SourceKind]:
        if origin is None:
            return None, None, SourceKind.UNKNOWN
        classes = origin["classes"]
        kind = source_kind(classes, False)
        if kind is SourceKind.UNKNOWN and origin["uri"] and self._is_a(origin["uri"], LAKE):
            kind = SourceKind.LAKE
        area = self.shapes.get(origin["qid"])
        area = area if is_area(area) else None
        lake = None
        if kind is SourceKind.LAKE:
            outline = self.shape(origin["qid"], AREAS)
            lake = outline if is_area(outline) else None
            area = None
        return area, lake, kind

    def _ends(self, river, origin, area, lake, mouth_row, mouth_classes, mouth_is_river, mouth_river, tributaries) -> tuple[Point, Point]:
        first, second = river_geometry.river_ends(river_geometry.main_pieces(river))
        origin_point = as_point(point_of(origin["c"])) if origin else None
        mouth_point = as_point(point_of(self._point_of_item(mouth_row["m"]))) if mouth_row else None
        if origin_point:
            reference = lake.union_all() if lake is not None else area.union_all() if area is not None else None
            upstream, mouth = sorted((first, second), key=lambda p: (round(p.distance(reference), 3) if reference is not None else 0, p.distance(origin_point)))
        elif mouth_river is not None:
            mouth, upstream = sorted((first, second), key=lambda p: p.distance(mouth_river.union_all()))
        elif mouth_point and tributaries and mouth_classes and not mouth_classes & MARINE and not mouth_is_river:
            centre = MultiPoint([Point(c) for lines in tributaries.values() for line in lines.explode(index_parts=False) for c in list(line.coords)[::10]]).centroid
            upstream, mouth = sorted((first, second), key=lambda p: p.distance(centre))
        elif mouth_point:
            mouth, upstream = sorted((first, second), key=lambda p: p.distance(mouth_point))
        else:
            upstream, mouth = first, second
        return upstream, mouth

    def _point_of_item(self, uri: str) -> str | None:
        rows = self._rows(f"SELECT ?c WHERE {{ wd:{qid_of(uri)} wdt:P625 ?c }}")
        return rows[0]["c"] if rows else None

    def _far_from(self, upstream: Point, area: gpd.GeoSeries, river: gpd.GeoSeries) -> bool:
        x0, y0, x1, y1 = river.total_bounds
        return upstream.distance(area.union_all()) > 0.05 * ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5

    def _source(self, origin: dict | None, kind: SourceKind, upstream: Point) -> Source | None:
        if origin is None:
            return None
        point = (upstream.x, upstream.y) if kind is SourceKind.UNKNOWN else point_of(origin["c"])
        return Source(source_label(origin["label"], origin["classes"]), origin["qid"], kind, tuple(sorted(origin["classes"])), point)

    def _mouth(self, row: dict | None, classes: set[str], is_river: bool, dried: bool, sink: Sink | None) -> Mouth | None:
        if row is None:
            return None
        if is_river:
            kind = MouthKind.RIVER
        elif classes & MARINE or sink is None:
            kind = MouthKind.SEA
        elif dried:
            kind = MouthKind.DRIED
        elif classes & WATER_BODY:
            kind = MouthKind.LAKE
        elif classes & WETTED_LAND:
            kind = MouthKind.DELTA
        else:
            kind = MouthKind.LAND
        return Mouth(row["ml"], qid_of(row["m"]), kind, tuple(sorted(classes)), point_of(self._point_of_item(row["m"])))

    def _index(self, class_qid: str, subclasses: bool = True) -> list[tuple[Landmark, Point]]:
        key = f"{class_qid}{subclasses}"
        if key not in self._landmarks:
            kind = "wdt:P31/wdt:P279*" if subclasses else "wdt:P31"
            rows = self._rows(
                f"SELECT DISTINCT ?g ?gl ?c ?n WHERE {{ ?g {kind} wd:{class_qid} ; wdt:P625 ?c ; wikibase:sitelinks ?n . FILTER(?n >= {MIN_LINKS}) ?g rdfs:label ?gl FILTER(lang(?gl)='en') }} ORDER BY DESC(?n)"
            )
            self._landmarks[key] = [(Landmark(row["gl"], qid_of(row["g"]), point_of(row["c"])), Point(point_of(row["c"]))) for row in rows]
        return self._landmarks[key]

    def _near(self, river: gpd.GeoSeries, candidates: list[tuple[Landmark, Point]], skip: str | None = None) -> tuple[Landmark, ...]:
        line = river.union_all()
        near = {landmark.qid: landmark for landmark, point in candidates if landmark.qid != skip and point.distance(line) < REACH}
        return tuple(list(near.values())[:MAX_PER_KIND])

    def _along(self, river: gpd.GeoSeries, class_qid: str) -> tuple[Landmark, ...]:
        return self._near(river, self._index(class_qid))

    def _gorges(self, river: gpd.GeoSeries) -> tuple[Landmark, ...]:
        return self._near(river, self._index(CANYON, False) + self._index(GORGE, False))

    def _path_lakes(self, qid: str, river: gpd.GeoSeries, origin: dict | None) -> tuple[PathLake, ...]:
        rows = self._rows(
            f"""SELECT DISTINCT ?f ?fl ?c ?n WHERE {{ {{ ?f wdt:P206 wd:{qid} }} UNION {{ ?f wdt:P200 wd:{qid} }} UNION {{ ?f wdt:P201 wd:{qid} }} UNION {{ wd:{qid} wdt:P200|wdt:P201 ?f }}
            ?f wdt:P31/wdt:P279* ?k . VALUES ?k {{ wd:{LAKE} wd:Q131681 }} ?f wdt:P625 ?c ; wikibase:sitelinks ?n ; rdfs:label ?fl FILTER(lang(?fl)='en' && ?n >= {MIN_LINKS}) }} ORDER BY DESC(?n) LIMIT {LAKE_QUERY_LIMIT}"""
        )
        line = river.union_all()
        found: dict[str, PathLake] = {}
        for row in rows:
            qid_item = qid_of(row["f"])
            point = point_of(row["c"])
            if qid_item != (origin and origin["qid"]) and qid_item not in found and Point(point).distance(line) < REACH:
                outline = self.shape(qid_item, AREAS)
                found[qid_item] = PathLake(Landmark(row["fl"], qid_item, point), outline if is_area(outline) else None)
        return tuple(list(found.values())[:MAX_PER_KIND])


def span_of(shape: gpd.GeoSeries) -> float:
    x0, y0, x1, y1 = shape.total_bounds
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5


def oversized(area: gpd.GeoSeries, river: gpd.GeoSeries) -> bool:
    return span_of(area) > OVERSIZED_AREA * span_of(river)


def river_geometry_of(geometry: gpd.GeoSeries) -> gpd.GeoSeries | None:
    parts = [part for part in geometry.make_valid().explode(index_parts=False) if part.geom_type in LINES and not part.is_empty]
    return gpd.GeoSeries(parts, crs=CRS) if parts else None
