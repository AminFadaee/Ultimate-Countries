import re
import urllib.parse
from collections import defaultdict

import geopandas as gpd
from shapely.geometry import LineString, Point, shape

from geography.data import NaturalEarth
from geography.features.kinds import Kind
from geography.features.model import Feature
from geography.features.spatial import CRS, CountryLocator
from geography.features.wikidata import FeatureQueries, ItemFacts
from geography.places import Nominatim
from geography.render import EQUAL_AREA
from geography.sources.ecoregions import rainforest_blocks
from geography.sources.marine_regions import fetch_sea_areas
from geography.sources.natural_earth_physical import PhysicalLayer, load_layer
from geography.sources.wikidata import Wikidata
from geography.sources.wikipedia import Infobox, Wikipedia

NATURAL_EARTH_KINDS = {
    "Continent": (Kind.CONTINENT, 0),
    "Range/mtn": (Kind.MOUNTAIN_RANGE, 3),
    "Desert": (Kind.DESERT, 3),
    "Plateau": (Kind.PLATEAU, 2),
    "Plain": (Kind.PLAIN, 2),
    "Lowland": (Kind.PLAIN, 3),
    "Basin": (Kind.BASIN, 2),
    "Valley": (Kind.VALLEY, 3),
    "Delta": (Kind.DELTA, 3),
    "Wetlands": (Kind.WETLAND, 4),
    "Pen/cape": (Kind.PENINSULA, 2),
    "Peninsula": (Kind.PENINSULA, 3),
    "Isthmus": (Kind.ISTHMUS, 3),
    "Geoarea": (Kind.REGION, 2),
    "Gorge": (Kind.CANYON, 5),
}
RAINFOREST_NAMERS = ("Basin", "Island")
MAX_LAKE_RANK = 1
MAX_RIVER_RANK = 4
PEAK_CLASS = "mountain"
REEF_CLASS = "reef"

MIN_REGION_LINKS = 40
MIN_SEA_LINKS = 90
MIN_LAKE_LINKS = 60
MIN_RIVER_LINKS = 100
MIN_PEAK_LINKS = 60
MIN_MOUNTAIN_LINKS = 90
MIN_VOLCANO_LINKS = 65
MIN_WATERFALL_LINKS = 40
MIN_CANYON_LINKS = 40
MIN_FOREST_LINKS = 40
MIN_CULTURAL_REGION_LINKS = 100
MIN_HERITAGE_LINKS = 80
MIN_STRUCTURE_LINKS = 90
MIN_REEF_LINKS = 40
MIN_CANAL_LINKS = 50
MIN_RAINFOREST_KM2 = 500_000
MIN_REGION_MEMBERS = 2
MIN_REGION_KM2 = 50_000

OCEAN = "Q9430"
SEA_CLASSES = ("Q165", "Q1322134", "Q39594", "Q37901")
MOUNTAIN = "Q8502"
VOLCANO = "Q8072"
WATERFALL = "Q34038"
CANYON = "Q150784"
CANAL = "Q12284"
FOREST_CLASSES = ("Q9444", "Q1968993", "Q199403", "Q101998")
PROTECTED_AREA = "Q473972"
REGION_CLASSES = ("Q3502482", "Q82794")
HUMAN_SETTLEMENT = "Q486972"
STRUCTURE_CLASSES = (
    "Q12518", "Q12280", "Q179700", "Q11303", "Q23413", "Q16560", "Q44539", "Q32815", "Q2977", "Q4989906",
    "Q839954", "Q12516", "Q54831", "Q16748868", "Q57831", "Q162875", "Q200141", "Q57821",
)
ARCHAEOLOGICAL_SITE = "Q839954"
SHIP = "Q11446"
ADMINISTRATIVE_AREA = "Q56061"
LANDMARK_CLASSES = ("Q811979", "Q839954")
DESERT = "Q8514"
PENINSULA = "Q34763"
RIVER = "Q4022"
NOT_CULTURAL_REGIONS = ("Q46831", "Q34763", "Q23442", "Q56061", "Q5107")
PRIME_MERIDIAN = "Q3401774"
PRIME_MERIDIAN_NAME = "Prime Meridian"
BASIN_SUFFIX = re.compile(r"\s+basin$", re.IGNORECASE)


def article_title(article_url: str) -> str:
    return urllib.parse.unquote(article_url.rsplit("/wiki/", 1)[-1]).replace("_", " ")


def point(longitude: float, latitude: float) -> gpd.GeoSeries:
    return gpd.GeoSeries([Point(longitude, latitude)], crs=CRS)


def dissolved(frame: gpd.GeoDataFrame, id_column: str) -> dict[str, gpd.GeoSeries]:
    return {
        qid: gpd.GeoSeries([group.geometry.union_all()], crs=frame.crs).to_crs(CRS)
        for qid, group in frame[frame[id_column].notna()].groupby(id_column)
    }


class Catalog:
    def __init__(self, session, wikidata: Wikidata, wikipedia: Wikipedia, locator: CountryLocator, data: NaturalEarth):
        self.session = session
        self.wikidata = wikidata
        self.queries = FeatureQueries(wikidata)
        self.wikipedia = wikipedia
        self.locator = locator
        self.data = data
        self.facts: dict[str, ItemFacts] = {}

    def collect(self) -> list[Feature]:
        features = [
            *self.cultural_regions(),
            *self.natural_earth_regions(),
            *self.reefs(),
            *self.seas(),
            *self.lakes(),
            *self.rivers(),
            *self.mountains(),
            *self.from_ranking(Kind.WATERFALL, (WATERFALL,), MIN_WATERFALL_LINKS),
            *self.from_ranking(Kind.CANYON, (CANYON,), MIN_CANYON_LINKS),
            *self.rainforests(),
            *self.landmarks(),
            *self.canals(),
            *self.lines(),
        ]
        unique = {}
        for feature in features:
            unique.setdefault(feature.key, feature)
        for feature in unique.values():
            feature.name = feature.name[:1].upper() + feature.name[1:]
        return list(unique.values())

    def known(self, qids: set[str]) -> dict[str, ItemFacts]:
        missing = qids - self.facts.keys()
        if missing:
            self.facts.update(self.queries.facts(missing))
        return {qid: self.facts[qid] for qid in qids if qid in self.facts}

    def feature(self, qid: str, kind: Kind, geometry: gpd.GeoSeries) -> Feature:
        facts = self.facts[qid]
        return Feature(qid, facts.label, kind, geometry, facts.sitelinks, qid)

    def famous(self, shapes: dict[str, gpd.GeoSeries], kind: Kind, min_links: int) -> list[Feature]:
        facts = self.known(set(shapes))
        return [self.feature(qid, kind, geometry) for qid, geometry in shapes.items() if qid in facts and facts[qid].sitelinks >= min_links]

    def natural_earth_regions(self) -> list[Feature]:
        regions = load_layer(PhysicalLayer.REGIONS)
        features = []
        for featurecla, (kind, max_rank) in NATURAL_EARTH_KINDS.items():
            selected = regions[(regions["featurecla"] == featurecla) & (regions["scalerank"] <= max_rank)]
            features.extend(self.famous(dissolved(selected, "wikidataid"), kind, MIN_REGION_LINKS))
        qids = {feature.qid for feature in features}
        deserts = self.queries.instances_of(qids, DESERT)
        peninsulas = self.queries.instances_of(qids, PENINSULA)
        administrative = self.queries.instances_of(qids, ADMINISTRATIVE_AREA)
        for feature in features:
            if feature.qid in peninsulas:
                feature.kind = Kind.PENINSULA
        return [
            feature
            for feature in features
            if (feature.kind is not Kind.DESERT or feature.qid in deserts)
            and (feature.kind is not Kind.REGION or feature.qid not in administrative)
        ]

    def reefs(self) -> list[Feature]:
        marine = load_layer(PhysicalLayer.MARINE)
        return self.famous(dissolved(marine[marine["featurecla"] == REEF_CLASS], "wikidataid"), Kind.REEF, MIN_REEF_LINKS)

    def seas(self) -> list[Feature]:
        areas = fetch_sea_areas(self.session)
        items = self.queries.sea_items(set(areas["mrgid"]))
        parents = {parent for item in items.values() for parent in item.parents}
        oceans = self.queries.instances_of(parents | {item.qid for item in items.values()}, OCEAN)
        seas = set().union(*(self.queries.instances_of(parents, cls) for cls in SEA_CLASSES))
        parent_labels = {qid: facts.label for qid, facts in self.known(parents).items()}

        members: dict[str, list] = defaultdict(list)
        for row in areas.itertuples():
            item = items.get(row.mrgid)
            if item is None:
                continue
            members[item.qid].append(row.geometry)
            for parent in item.parents:
                label = parent_labels.get(parent)
                if (parent in oceans and label and item.label.endswith(label)) or parent in seas - oceans:
                    members[parent].append(row.geometry)
        shapes = {qid: gpd.GeoSeries([gpd.GeoSeries(parts, crs=CRS).union_all()], crs=CRS) for qid, parts in members.items()}

        marine = load_layer(PhysicalLayer.MARINE)
        uncovered = marine[(marine["featurecla"] == "sea") & marine["wikidataid"].notna() & ~marine["wikidataid"].isin(list(shapes))]
        shapes.update(dissolved(uncovered, "wikidataid"))
        facts = self.known(set(shapes))
        return [
            self.feature(qid, Kind.OCEAN if qid in oceans else Kind.SEA, geometry)
            for qid, geometry in shapes.items()
            if qid in facts and facts[qid].sitelinks >= MIN_SEA_LINKS
        ]

    def lakes(self) -> list[Feature]:
        lakes = load_layer(PhysicalLayer.LAKES)
        return self.famous(dissolved(lakes[lakes["scalerank"] <= MAX_LAKE_RANK], "wikidataid"), Kind.LAKE, MIN_LAKE_LINKS)

    def rivers(self) -> list[Feature]:
        rivers = load_layer(PhysicalLayer.RIVERS)
        selected = rivers[(rivers["featurecla"] == "River") & (rivers["scalerank"] <= MAX_RIVER_RANK)]
        return self.famous(dissolved(selected, "wikidataid"), Kind.RIVER, MIN_RIVER_LINKS)

    def mountains(self) -> list[Feature]:
        peaks = load_layer(PhysicalLayer.ELEVATION_POINTS)
        from_points = {row.wikidataid for row in peaks.itertuples() if isinstance(row.wikidataid, str) and row.featurecla == PEAK_CLASS}
        famous_points = {qid for qid, facts in self.known(from_points).items() if facts.sitelinks >= MIN_PEAK_LINKS}
        ranked = set(self.queries.ranked((MOUNTAIN,), MIN_MOUNTAIN_LINKS)) | set(self.queries.ranked((VOLCANO,), MIN_VOLCANO_LINKS))
        candidates = famous_points | ranked
        volcanoes = self.queries.instances_of(candidates, VOLCANO)
        facts = self.known(candidates)
        return [
            self.feature(qid, Kind.VOLCANO if qid in volcanoes else Kind.MOUNTAIN, point(*facts[qid].coordinates))
            for qid in candidates
            if qid in facts and facts[qid].coordinates
        ]

    def from_ranking(self, kind: Kind, classes: tuple[str, ...], min_links: int) -> list[Feature]:
        ranked = self.queries.ranked(classes, min_links)
        facts = self.known(set(ranked))
        return [self.feature(qid, kind, point(*facts[qid].coordinates)) for qid in ranked if qid in facts and facts[qid].coordinates]

    def rainforests(self) -> list[Feature]:
        blocks = rainforest_blocks(MIN_RAINFOREST_KM2)
        ranked = set(self.queries.ranked(FOREST_CLASSES, MIN_FOREST_LINKS))
        forests = ranked - self.queries.instances_of(ranked, PROTECTED_AREA)
        facts = {qid: found for qid, found in self.known(forests).items() if found.coordinates}
        regions = load_layer(PhysicalLayer.REGIONS)
        namers = regions[regions["featurecla"].isin(RAINFOREST_NAMERS) & regions["wikidataid"].notna()].to_crs(EQUAL_AREA)
        features = []
        for block in blocks:
            area = gpd.GeoSeries([block], crs=CRS)
            inside = [qid for qid, found in facts.items() if block.contains(Point(*found.coordinates))]
            if inside:
                features.append(self.feature(max(inside, key=lambda qid: facts[qid].sitelinks), Kind.RAINFOREST, area))
            elif name := self._rainforest_name(area, namers):
                features.append(Feature(f"rainforest:{name}", name, Kind.RAINFOREST, area))
        return features

    def _rainforest_name(self, area: gpd.GeoSeries, namers: gpd.GeoDataFrame) -> str | None:
        block = area.to_crs(EQUAL_AREA).iloc[0]
        overlaps = namers.geometry.intersection(block).area / block.area
        if overlaps.empty or overlaps.max() < 0.25:
            return None
        qid = namers.loc[overlaps.idxmax(), "wikidataid"]
        label = self.known({qid}).get(qid)
        return f"{BASIN_SUFFIX.sub('', label.label)} rainforest" if label else None

    def cultural_regions(self) -> list[Feature]:
        ranked = self.queries.ranked(REGION_CLASSES, MIN_CULTURAL_REGION_LINKS)
        physical = set().union(*(self.queries.instances_of(set(ranked), cls) for cls in NOT_CULTURAL_REGIONS))
        ranked = [qid for qid in ranked if qid not in physical]
        facts = self.known(set(ranked))
        our_names = {country.name for country in self.locator.ours.values()}
        features = []
        for qid in ranked:
            article = facts.get(qid) and facts[qid].wikipedia
            if not article or facts[qid].label in our_names:
                continue
            members = [member for member in self._infobox(article).members if member in our_names]
            if len(set(members)) < MIN_REGION_MEMBERS:
                continue
            shape = self.locator.countries_in(members)
            if shape.to_crs(EQUAL_AREA).area.sum() >= MIN_REGION_KM2 * 1e6:
                features.append(self.feature(qid, Kind.REGION, shape))
        return features

    def landmarks(self) -> list[Feature]:
        heritage = self.queries.ranked_heritage(MIN_HERITAGE_LINKS)
        structures = self.queries.ranked(STRUCTURE_CLASSES, MIN_STRUCTURE_LINKS)
        candidates = set(heritage) | set(structures)
        built = set().union(*(self.queries.instances_of(candidates, cls) for cls in LANDMARK_CLASSES))
        settlements = self.queries.instances_of(built, HUMAN_SETTLEMENT)
        living = self.queries.inhabited(settlements) | (settlements - self.queries.instances_of(settlements, ARCHAEOLOGICAL_SITE))
        excluded = living | self.queries.instances_of(built, SHIP)
        facts = self.known(built)
        return [
            self.feature(qid, Kind.LANDMARK, point(*facts[qid].coordinates))
            for qid in built - excluded
            if qid in facts and facts[qid].coordinates
        ]

    def canals(self) -> list[Feature]:
        ranked = self.queries.ranked((CANAL,), MIN_CANAL_LINKS)
        rivers_too = self.queries.instances_of(set(ranked), RIVER)
        ranked = [qid for qid in ranked if qid not in rivers_too]
        facts = self.known(set(ranked))
        rivers = dissolved(load_layer(PhysicalLayer.RIVERS), "wikidataid")
        relations = self.wikidata.osm_relations(set(ranked))
        geometries = Nominatim().relations(list(relations.values()))
        features = []
        for qid in ranked:
            if qid not in facts:
                continue
            geometry = rivers.get(qid)
            if geometry is None and (feature := geometries.get(relations.get(qid, ""))):
                geometry = gpd.GeoSeries([shape(feature["geometry"])], crs=CRS)
            if geometry is None and facts[qid].coordinates:
                geometry = point(*facts[qid].coordinates)
            if geometry is not None:
                features.append(self.feature(qid, Kind.CANAL, geometry))
        return features

    def lines(self) -> list[Feature]:
        lines = load_layer(PhysicalLayer.LINES)
        shapes = dissolved(lines, "wikidataid")
        features = self.famous(shapes, Kind.LINE, 0)
        meridian = gpd.GeoSeries([LineString([(0, latitude) for latitude in range(-90, 91)])], crs=CRS)
        features.append(Feature(PRIME_MERIDIAN, PRIME_MERIDIAN_NAME, Kind.LINE, meridian, qid=PRIME_MERIDIAN))
        return features

    def _infobox(self, article_url: str) -> Infobox:
        return self.wikipedia.infobox(article_title(article_url))
