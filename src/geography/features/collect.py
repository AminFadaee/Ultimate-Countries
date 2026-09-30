import json
import logging
import pathlib
from dataclasses import asdict, dataclass

import requests

from geography.data import NaturalEarth
from geography.features.catalog import Catalog, article_title
from geography.features.kinds import WATER_KINDS, Kind
from geography.features.maps import map_job, render_feature_maps
from geography.features.model import Feature, Photo
from geography.features.spatial import POINT_M, CountryLocator, load_our_countries
from geography.naming import slugify
from geography.sources import commons
from geography.sources.wikidata import Wikidata
from geography.sources.wikipedia import Wikipedia

BORDER_RADIUS_M = {Kind.WATERFALL: 6_000, Kind.MOUNTAIN: 8_000, Kind.VOLCANO: 8_000}
INFOBOX_KINDS = {Kind.LANDMARK, Kind.CANAL}
WITHOUT_COUNTRIES = {Kind.CONTINENT, Kind.OCEAN}

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
        self._add_countries(features, locator)
        self._add_cities(features, catalog)
        self._add_construction(features)
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
            feature.image = facts.image
            feature.height_m = facts.height_m
            feature.elevation_m = facts.elevation_m
            feature.length_km = facts.length_km
            feature.area_km2 = facts.area_km2
            feature.world_heritage = facts.world_heritage

    def _add_countries(self, features: list[Feature], locator: CountryLocator) -> None:
        for feature in features:
            shape = feature.kind.spec.shape
            if feature.kind in WITHOUT_COUNTRIES:
                continue
            if feature.geometry.geom_type.iloc[0] == "Point":
                location = feature.geometry.iloc[0]
                feature.countries = locator.at_point(location.x, location.y, BORDER_RADIUS_M.get(feature.kind, POINT_M))
            elif shape.is_line:
                feature.countries = locator.by_line(feature.geometry)
            elif feature.kind in WATER_KINDS:
                feature.countries = locator.by_coast(feature.geometry)
            else:
                feature.countries = locator.by_area(feature.geometry)

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

    def _add_photos(self, features: list[Feature], slugs: dict[str, str]) -> None:
        previous = self._previous_photos()
        for feature in features:
            if not (feature.kind.spec.photo_card and feature.image):
                continue
            path = self.paths.photos / f"{slugs[feature.key]}.jpg"
            known = previous.get(feature.key)
            if known and known.source == feature.image and (self.paths.root / known.file).exists():
                feature.photo = known
                continue
            try:
                thumbnail = commons.thumbnail_url(self.session, feature.image)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(commons.download_thumbnail(self.session, thumbnail))
            except (requests.RequestException, KeyError) as error:
                logger.warning("No photo for %s: %s", feature.name, error)
                continue
            feature.photo = Photo(self.paths.relative(path), feature.image)

    def _previous_photos(self) -> dict[str, Photo]:
        found = {}
        for path in self.paths.features.glob("*.json"):
            document = json.loads(path.read_text())
            if photo := document.get("photo"):
                found[document["key"]] = Photo(photo["file"], photo["source"])
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
        return len(documents)


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
        "world_heritage": feature.world_heritage,
        "tags": feature.tags,
        "map": feature.map,
        "photo": asdict(feature.photo) if feature.photo else None,
        "wikipedia": feature.wikipedia,
        "fame": feature.sitelinks,
    }
