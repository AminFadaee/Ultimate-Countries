import json
import logging
import pathlib
from types import SimpleNamespace

import geopandas as gpd
from shapely.geometry import Point, mapping, shape

from geography.countrydeck.cities import near_mainland_features, within_territory
from geography.countrydeck.maps import (
    PlaceMapJob,
    city_labels,
    deck_cities,
    render_place_maps,
)
from geography.countrydeck.names import named
from geography.countrydeck.selection import (
    inscribed,
    natural_sites,
    select_places,
    wholes,
)
from geography.data import CONTESTED_TYPES, NaturalEarth
from geography.features.catalog import complete_rivers, drawn_km, natural_earth_shapes
from geography.features.collect import (
    PlacesCollector,
    PlacesOptions,
    PlacesPaths,
    unique_slugs,
)
from geography.features.kinds import Kind, Shape
from geography.features.model import Feature
from geography.features.spatial import CRS, OurCountry
from geography.features.wikidata import FeatureQueries
from geography.naming import answer_path
from geography.places import Nominatim
from geography.sources.wikidata import Wikidata

logger = logging.getLogger(__name__)
MIN_DRAWN_SHARE = 0.4


def drawn_enough(river: Feature, length_km: float | None) -> bool:
    lines = river.geometry[river.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    drawn = drawn_km(lines) if not lines.empty else 0.0
    return drawn >= MIN_DRAWN_SHARE * length_km if length_km else drawn > 0


def resolve_shapes(wikidata: Wikidata, features: list[Feature]) -> None:
    shapes = natural_earth_shapes()
    pending = [feature for feature in features if feature.kind.spec.shape is not Shape.POINT and feature.qid not in shapes]
    relations = wikidata.osm_relations({feature.qid for feature in pending}) if pending else {}
    surveyed = Nominatim().relations(sorted(set(relations.values()))) if relations else {}
    for feature in features:
        if feature.kind.spec.shape is Shape.POINT:
            continue
        if feature.qid in shapes:
            feature.geometry = shapes[feature.qid]
        elif found := surveyed.get(relations.get(feature.qid, "")):
            feature.geometry = gpd.GeoSeries([shape(found["geometry"])], crs=CRS)


def without_parts(features: list[Feature], wholes: dict[str, set[str]]) -> list[Feature]:
    named = {feature.qid: feature.name.casefold() for feature in features}
    def duplicate(feature: Feature) -> bool:
        return any(whole in named and feature.name.casefold().startswith(named[whole]) for whole in wholes.get(feature.qid, ()))
    return [feature for feature in features if not duplicate(feature)]


def one_per_extreme(features: list[Feature], tiers: dict[str, str], order: dict[str, list[str]]) -> list[Feature]:
    dropped: set[str] = set()
    for tier, ranking in order.items():
        present = [qid for qid in ranking if any(feature.qid == qid for feature in features)]
        dropped |= {feature.qid for feature in features if tiers[feature.qid] == tier and feature.qid != (present[0] if present else None)}
    return [feature for feature in features if feature.qid not in dropped]


class CountryPlacesCollector(PlacesCollector):
    def __init__(self, directory: pathlib.Path, session, language: str):
        super().__init__(PlacesPaths(directory), session, language)

    def collect(self, data: NaturalEarth, code: str, country: dict, excluded: set[str]) -> list[Feature]:
        self.country_qid = country["id"]
        candidates, extremes = select_places(self.wikidata, country["id"], excluded)
        queries = FeatureQueries(self.wikidata)
        facts = queries.facts({candidate.qid for candidate in candidates})
        features = []
        for candidate in candidates:
            if candidate.qid not in facts:
                continue
            spot = facts[candidate.qid].coordinates
            geometry = gpd.GeoSeries([Point(*spot)], crs=CRS) if spot else gpd.GeoSeries([], crs=CRS)
            features.append(Feature(candidate.qid, "", candidate.kind, geometry, facts[candidate.qid].sitelinks, candidate.qid))
        tiers = {candidate.qid: candidate.tier for candidate in candidates}
        groups = {candidate.qid: candidate.group for candidate in candidates}
        resolve_shapes(self.wikidata, features)
        complete_rivers(self.wikidata, [feature for feature in features if feature.kind is Kind.RIVER], {qid: found.length_km for qid, found in facts.items()})
        features = self._kept("too little of the river drawn", features,
                              [feature for feature in features if feature.kind is not Kind.RIVER or drawn_enough(feature, facts[feature.qid].length_km)])
        features = self._kept("without a shape", features, [feature for feature in features if not feature.geometry.empty and not feature.geometry.union_all().is_empty])
        features = self._kept("too far from the mainland", features, near_mainland_features(data, code, within_territory(data, code, features)))
        features = self._kept("not the highest or lowest", features, one_per_extreme(features, tiers, extremes))
        names = named(self.wikidata, {feature.qid for feature in features}, self.language)
        host = OurCountry(country["id"], code, country["name"], country["region"], country["wikipedia_url"])
        for feature in features:
            feature.name, feature.native = names[feature.qid].name, names[feature.qid].native
            feature.countries, feature.continents = [host], [country["region"]]
        features = self._kept("without a name", features, [feature for feature in features if feature.name != feature.qid])
        features = without_parts(features, wholes(self.wikidata, {feature.qid for feature in features}))
        self._mark(features, data, tiers, groups)
        catalog = SimpleNamespace(facts=facts, queries=queries)
        self._add_facts(features, catalog)
        listed = inscribed(self.wikidata, {feature.qid for feature in features})
        for feature in features:
            feature.world_heritage = feature.world_heritage or feature.qid in listed
        self._add_cities(features, catalog)
        self._add_construction(features)
        self._add_descriptions(features)
        return features

    @staticmethod
    def _kept(reason: str, before: list[Feature], after: list[Feature]) -> list[Feature]:
        kept = {feature.qid for feature in after}
        for feature in before:
            if feature.qid not in kept:
                logger.info("Dropped %s (%s): %s", feature.qid, feature.kind.value, reason)
        return after

    def _mark(self, features: list[Feature], data: NaturalEarth, tiers: dict[str, str], groups: dict[str, str]) -> None:
        natural = natural_sites(self.wikidata, {feature.qid for feature in features if tiers[feature.qid] == "unesco"})
        contested = data.disputed_areas[data.disputed_areas["TYPE"].isin(CONTESTED_TYPES)].union_all()
        for feature in features:
            if groups.get(feature.qid):
                feature.extras["group"] = groups[feature.qid]
            if feature.qid in natural:
                feature.extras["natural_site"] = True
            if contested.contains(feature.geometry.union_all().representative_point()):
                feature.extras["disputed"] = True

    def finish(self, features: list[Feature]) -> dict[str, str]:
        self._save_shapes({feature.key: feature.geometry for feature in features})
        slugs = unique_slugs(features)
        self._add_photos(features, slugs)
        return slugs

    def draw_maps(self, features: list[Feature], slugs: dict[str, str], country: dict, host: str, rerender: bool, workers: int) -> list[Feature]:
        jobs = []
        art = self._river_art(features)
        surroundings = self._surroundings(features)
        cities = city_labels(deck_cities(self.paths.root), country)
        for feature in features:
            output = self.paths.maps / f"{slugs[feature.key]}.png"
            feature.map = self.paths.relative(output)
            if rerender or not (output.exists() and answer_path(output).exists()):
                photo = self.paths.root / feature.photo.file if feature.photo else None
                jobs.append(PlaceMapJob(feature.key, feature.kind.value, mapping(feature.geometry.union_all()), host, feature.name, photo, output, answer_path(output),
                                        art.get(feature.key), cities if feature.key in art else (), surroundings.get(feature.key)))
        by_key = {feature.key: feature for feature in features}
        for job, error in render_place_maps(jobs, workers):
            if error:
                logger.error("Map failed for %s: %s", job.label, error)
                by_key[job.key].map = None
        return [feature for feature in features if feature.map]

    def export(self, features: list[Feature], slugs: dict[str, str]) -> int:
        return self._export(features, slugs)

    def redraw_for(self, country: dict, code: str, options: PlacesOptions) -> None:
        self.country, self.code = country, code
        self.redraw(options)

    def _draw(self, features: list[Feature], slugs: dict[str, str], options: PlacesOptions) -> None:
        self.draw_maps(features, slugs, self.country, self.code, options.rerender_maps, options.workers)

    def _rebuild_shapes(self, documents: list[dict]) -> dict[str, gpd.GeoSeries]:
        facts = FeatureQueries(self.wikidata).facts({content["qid"] for content in documents})
        features = []
        for content in documents:
            spot = facts[content["qid"]].coordinates if content["qid"] in facts else None
            features.append(Feature(content["key"], content["name"], Kind(content["kind"]), gpd.GeoSeries([Point(*spot)] if spot else [], crs=CRS), qid=content["qid"]))
        resolve_shapes(self.wikidata, features)
        complete_rivers(self.wikidata, [feature for feature in features if feature.kind is Kind.RIVER], {qid: found.length_km for qid, found in facts.items()})
        return {feature.key: feature.geometry for feature in features}


def read_documents(directory: pathlib.Path) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted((directory / "places" / "features").glob("*.json"))]

