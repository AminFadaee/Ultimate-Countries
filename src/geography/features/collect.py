import json
import logging
import pathlib
from dataclasses import asdict, dataclass

import geopandas as gpd
import requests

from geography.data import NaturalEarth
from geography.features.catalog import Catalog, article_title
from geography.features.kinds import WATER_KINDS, Kind, Shape
from geography.features.maps import map_job, render_feature_maps
from geography.features.model import Feature, Photo
from geography.features.spatial import (
    CLAIM_REACH_M,
    LAKE_SHORE_M,
    POINT_M,
    CountryLocator,
    load_our_countries,
)
from geography.naming import slugify
from geography.render import EQUAL_AREA, PROJECTABLE_LATITUDE
from geography.sources import commons
from geography.sources.natural_earth_physical import PhysicalLayer, load_layer
from geography.sources.wikidata import Wikidata
from geography.sources.wikipedia import Wikipedia

BORDER_RADIUS_M = {Kind.WATERFALL: 6_000, Kind.MOUNTAIN: 8_000, Kind.VOLCANO: 8_000}
INFOBOX_KINDS = {Kind.LANDMARK, Kind.CANAL}
WITHOUT_COUNTRIES = {Kind.CONTINENT, Kind.OCEAN}
WITHOUT_CONTINENT = {Kind.OCEAN, Kind.LINE}
CLAIMED_SHAPES = {Shape.POINT, Shape.LINE}
AREA_TOLERANCE = 5
MIN_CONTINENT_SHARE = 0.1
CONTINENT_TAGS = {
    "AFRICA": "Africa",
    "ANTARCTICA": "Antarctica",
    "ASIA": "Asia",
    "AUSTRALIA": "Oceania",
    "EUROPE": "Europe",
    "NORTH AMERICA": "Americas",
    "SOUTH AMERICA": "Americas",
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlacesPaths:
    root: pathlib.Path

    @property
    def features(self) -> pathlib.Path:
        return self.root / "places" / "features"

    @property
    def maps(self) -> pathlib.Path:
        return self.root / "places" / "maps"

    @property
    def photos(self) -> pathlib.Path:
        return self.root / "places" / "photos"

    @property
    def countries(self) -> pathlib.Path:
        return self.root / "countries"

    def relative(self, path: pathlib.Path) -> str:
        return str(path.relative_to(self.root))


@dataclass(frozen=True)
class PlacesOptions:
    rerender_maps: bool = False
    workers: int = 4


class PlacesCollector:
    def __init__(self, paths: PlacesPaths, session: requests.Session):
        self.paths = paths
        self.session = session
        self.wikidata = Wikidata(session)
        self.wikipedia = Wikipedia(session)

    def run(self, options: PlacesOptions) -> None:
        data = NaturalEarth.load()
        locator = CountryLocator(data, load_our_countries(self.paths.countries))
        catalog = Catalog(self.session, self.wikidata, self.wikipedia, locator, data)
        features = catalog.collect()
        logger.info("%d places selected", len(features))

        self._add_facts(features, catalog)
        self._add_countries(features, locator, catalog)
        self._add_continents(features)
        self._add_cities(features, catalog)
        self._add_construction(features)
        self._add_descriptions(features)
        slugs = unique_slugs(features)
        self._add_photos(features, slugs)
        self._render_maps(features, slugs, options)
        count = self._export(features, slugs)
        logger.info("Exported %d places to %s", count, self.paths.features)

    def _add_facts(self, features: list[Feature], catalog: Catalog) -> None:
        for feature in features:
            facts = catalog.facts.get(feature.qid or "")
            if facts is None:
                continue
            feature.wikipedia = facts.wikipedia
            feature.images = facts.images
            feature.height_m = facts.height_m
            feature.elevation_m = facts.elevation_m
            feature.length_km = facts.length_km
            feature.area_km2 = facts.area_km2 if plausible_area(feature, facts.area_km2) else None
            feature.world_heritage = facts.world_heritage

    def _add_countries(self, features: list[Feature], locator: CountryLocator, catalog: Catalog) -> None:
        claimed = catalog.queries.countries({feature.qid for feature in features if feature.qid and feature.kind.spec.shape in CLAIMED_SHAPES})
        by_id = {country.id: country for country in locator.ours.values()}
        for feature in features:
            if feature.kind in WITHOUT_COUNTRIES or feature.countries:
                continue
            named = sorted((by_id[qid] for qid in claimed.get(feature.qid or "", []) if qid in by_id), key=lambda country: country.name)
            if self._contested(feature, locator):
                feature.countries = []
                continue
            located = self._located(feature, locator)
            feature.countries = located + [country for country in locator.near(named, feature.geometry, CLAIM_REACH_M) if country not in located]

    def _contested(self, feature: Feature, locator: CountryLocator) -> bool:
        location = feature.geometry.iloc[0]
        return location.geom_type == "Point" and locator.is_contested(location.x, location.y)

    def _located(self, feature: Feature, locator: CountryLocator) -> list:
        if feature.geometry.geom_type.iloc[0] == "Point":
            location = feature.geometry.iloc[0]
            return locator.at_point(location.x, location.y, BORDER_RADIUS_M.get(feature.kind, POINT_M))
        if feature.kind.spec.shape.is_line:
            return locator.by_line(feature.geometry)
        if feature.kind is Kind.LAKE:
            return locator.by_coast(feature.geometry, LAKE_SHORE_M)
        if feature.kind in WATER_KINDS:
            return locator.by_coast(feature.geometry)
        return locator.by_area(feature.geometry)

    def _add_continents(self, features: list[Feature]) -> None:
        regions = load_layer(PhysicalLayer.REGIONS)
        continents = regions[regions["featurecla"] == "Continent"].to_crs(EQUAL_AREA)
        for feature in features:
            if feature.kind in WITHOUT_CONTINENT:
                continue
            found = continents_of(feature.geometry.to_crs(EQUAL_AREA).union_all(), continents)
            feature.continents = sorted(found or {country.continent for country in feature.countries if country.continent})

    def _add_cities(self, features: list[Feature], catalog: Catalog) -> None:
        landmarks = {feature.qid: feature for feature in features if feature.kind is Kind.LANDMARK and feature.qid}
        for qid, cities in catalog.queries.cities(set(landmarks)).items():
            if len(cities) == 1:
                landmarks[qid].city = cities[0]

    def _add_construction(self, features: list[Feature]) -> None:
        for feature in features:
            if feature.kind in INFOBOX_KINDS and feature.wikipedia:
                infobox = self.wikipedia.infobox(article_title(feature.wikipedia))
                feature.built, feature.culture = infobox.built, infobox.culture

    def _add_descriptions(self, features: list[Feature]) -> None:
        for feature in features:
            if feature.wikipedia:
                feature.description = self.wikipedia.first_sentence(article_title(feature.wikipedia))

    def _add_photos(self, features: list[Feature], slugs: dict[str, str]) -> None:
        previous = self._previous_photos()
        for feature in features:
            if feature.kind.spec.photo_card and feature.images:
                path = self.paths.photos / f"{slugs[feature.key]}.jpg"
                feature.photo = self._photo_for(feature, path, previous.get(feature.key))

    def _photo_for(self, feature: Feature, path: pathlib.Path, known: Photo | None) -> Photo | None:
        relative = self.paths.relative(path)
        stored = known is not None and known.file == relative and (self.paths.root / relative).exists()
        if known and stored and known.page and known.source in feature.images:
            return known
        for source in feature.images:
            try:
                image = commons.describe_image(self.session, source)
                if image is None:
                    continue
                if not (stored and known.source == source):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(commons.download_thumbnail(self.session, image.thumbnail_url))
            except (requests.RequestException, KeyError) as error:
                logger.warning("No photo for %s: %s", feature.name, error)
                continue
            return Photo(relative, source, image.author, image.license, image.license_url, image.page)
        logger.info("No usable photo for %s", feature.name)
        return None

    def _previous_photos(self) -> dict[str, Photo]:
        found = {}
        for path in self.paths.features.glob("*.json"):
            document = json.loads(path.read_text())
            if photo := document.get("photo"):
                found[document["key"]] = Photo(**{**photo, "author": commons.author_of(photo["author"])})
        return found

    def _render_maps(self, features: list[Feature], slugs: dict[str, str], options: PlacesOptions) -> None:
        jobs = []
        for feature in features:
            output = self.paths.maps / f"{slugs[feature.key]}.png"
            feature.map = self.paths.relative(output)
            if options.rerender_maps or not output.exists():
                jobs.append(map_job(feature, output))
        by_key = {feature.key: feature for feature in features}
        for job, error in render_feature_maps(jobs, options.workers):
            if error:
                logger.error("Map failed for %s: %s", by_key[job.key].name, error)
                by_key[job.key].map = None

    def _export(self, features: list[Feature], slugs: dict[str, str]) -> int:
        self.paths.features.mkdir(parents=True, exist_ok=True)
        documents = {slugs[feature.key]: document(feature) for feature in features}
        referenced = {self.paths.features / f"{slug}.json" for slug in documents}
        referenced |= {self.paths.root / content["map"] for content in documents.values() if content["map"]}
        referenced |= {self.paths.root / content["photo"]["file"] for content in documents.values() if content["photo"]}
        for directory, suffix in ((self.paths.features, ".json"), (self.paths.maps, ".png"), (self.paths.photos, ".jpg")):
            for stale in directory.glob(f"*{suffix}"):
                if stale not in referenced:
                    stale.unlink()
        for slug, content in documents.items():
            path = self.paths.features / f"{slug}.json"
            text = json.dumps(content, ensure_ascii=False, indent=2) + "\n"
            if not path.exists() or path.read_text() != text:
                path.write_text(text)
        (self.paths.root / "places" / "CREDITS.md").write_text(credits(list(documents.values())))
        return len(documents)


def measure(shape) -> float:
    if shape.geom_type in ("Point", "MultiPoint"):
        return 0.0 if shape.is_empty else 1.0
    return shape.length if shape.geom_type in ("LineString", "MultiLineString") else shape.area


def continents_of(shape, continents: gpd.GeoDataFrame) -> set[str]:
    total = measure(shape)
    found = set()
    for row in continents.itertuples():
        tag = CONTINENT_TAGS.get(row.name)
        if tag and total and measure(shape.intersection(row.geometry)) / total >= MIN_CONTINENT_SHARE:
            found.add(tag)
    return found


def plausible_area(feature: Feature, area_km2: float | None) -> bool:
    if area_km2 is None or feature.kind.spec.shape is not Shape.AREA:
        return True
    visible = feature.geometry.clip_by_rect(-180, -PROJECTABLE_LATITUDE, 180, PROJECTABLE_LATITUDE)
    shape_km2 = visible.to_crs(EQUAL_AREA).area.sum() / 1e6
    return not shape_km2 or 1 / AREA_TOLERANCE <= area_km2 / shape_km2 <= AREA_TOLERANCE


def credits(documents: list[dict]) -> str:
    rows = []
    for content in sorted(documents, key=lambda content: content["name"]):
        if photo := content["photo"]:
            licence = f"[{photo['license']}]({photo['license_url']})" if photo["license_url"] else photo["license"]
            author = photo["author"].replace("|", "/")
            rows.append(f"| {content['name']} | {author} | {licence} | [Wikimedia Commons]({photo['page']}) |")
    header = [
        "# Photo credits",
        "",
        "The photos in Ultimate Places come from Wikimedia Commons, each under the licence given on its Commons page.",
        "",
        "| Place | Author | Licence | Source |",
        "|---|---|---|---|",
    ]
    return "\n".join([*header, *rows]) + "\n"


def unique_slugs(features: list[Feature]) -> dict[str, str]:
    slugs, taken = {}, set()
    for feature in sorted(features, key=lambda feature: -feature.sitelinks):
        slug = slugify(feature.name)
        if slug in taken:
            slug = f"{slug}_{slugify(feature.kind.value)}"
        taken.add(slug)
        slugs[feature.key] = slug
    return slugs


def document(feature: Feature) -> dict:
    return {
        "key": feature.key,
        "qid": feature.qid,
        "name": feature.name,
        "kind": feature.kind.value,
        "countries": [country.name for country in feature.countries],
        "continents": feature.continents,
        "city": feature.city,
        "height_m": feature.height_m,
        "elevation_m": feature.elevation_m,
        "length_km": feature.length_km,
        "area_km2": feature.area_km2,
        "built": feature.built,
        "culture": feature.culture,
        "description": feature.description,
        "world_heritage": feature.world_heritage,
        "tags": feature.tags,
        "map": feature.map,
        "photo": asdict(feature.photo) if feature.photo else None,
        "wikipedia": feature.wikipedia,
        "fame": feature.sitelinks,
    }
