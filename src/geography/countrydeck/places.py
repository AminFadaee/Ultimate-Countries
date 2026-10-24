import json
import logging
import pathlib
from types import SimpleNamespace

import geopandas as gpd
from shapely.geometry import Point, mapping, shape

from geography.countrydeck.cities import near_mainland_features
from geography.countrydeck.maps import PlaceMapJob, render_place_maps
from geography.countrydeck.names import named
from geography.countrydeck.selection import natural_sites, select_places
from geography.data import CONTESTED_TYPES, NaturalEarth
from geography.features.catalog import dissolved
from geography.features.collect import PlacesCollector, PlacesPaths, unique_slugs
from geography.features.kinds import Shape
from geography.features.model import Feature
from geography.features.spatial import CRS, OurCountry
from geography.features.wikidata import FeatureQueries
from geography.naming import answer_path
from geography.places import Nominatim
from geography.sources.natural_earth_physical import PhysicalLayer, load_layer
from geography.sources.wikidata import Wikidata

logger = logging.getLogger(__name__)
SHAPED_LAYERS = (PhysicalLayer.RIVERS, PhysicalLayer.LAKES, PhysicalLayer.REGIONS, PhysicalLayer.MARINE)


def natural_earth_shapes() -> dict[str, gpd.GeoSeries]:
    shapes: dict[str, gpd.GeoSeries] = {}
    for layer in SHAPED_LAYERS:
        frame = load_layer(layer)
        if layer is PhysicalLayer.RIVERS:
            by_name = frame.groupby(frame["name"].str.casefold())["wikidataid"].transform(lambda ids: ids.dropna().iloc[0] if ids.notna().any() else None)
            frame["wikidataid"] = frame["wikidataid"].fillna(by_name)
        for qid, geometry in dissolved(frame, "wikidataid").items():
            shapes.setdefault(qid, geometry)
    return shapes


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


def one_per_extreme(features: list[Feature], tiers: dict[str, str], order: dict[str, list[str]]) -> list[Feature]:
    dropped: set[str] = set()
    for tier, ranking in order.items():
        present = [qid for qid in ranking if any(feature.qid == qid for feature in features)]
        dropped |= {feature.qid for feature in features if tiers[feature.qid] == tier and feature.qid != (present[0] if present else None)}
    return [feature for feature in features if feature.qid not in dropped]


class CountryPlacesCollector(PlacesCollector):
    def __init__(self, directory: pathlib.Path, session):
        super().__init__(PlacesPaths(directory), session)

    def collect(self, data: NaturalEarth, code: str, country: dict, language: str, excluded: set[str]) -> list[Feature]:
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
        features = [feature for feature in features if not feature.geometry.empty and not feature.geometry.union_all().is_empty]
        features = near_mainland_features(data, code, features)
        features = one_per_extreme(features, tiers, extremes)
        names = named(self.wikidata, {feature.qid for feature in features}, language)
        host = OurCountry(country["id"], code, country["name"], country["region"], country["wikipedia_url"])
        for feature in features:
            feature.name, feature.native = names[feature.qid].name, names[feature.qid].native
            feature.countries, feature.continents = [host], [country["region"]]
        features = [feature for feature in features if feature.name != feature.qid]
        self._mark(features, data, tiers, groups)
        catalog = SimpleNamespace(facts=facts, queries=queries)
        self._add_facts(features, catalog)
        self._add_cities(features, catalog)
        self._add_construction(features)
        self._add_descriptions(features)
        return features

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
        slugs = unique_slugs(features)
        self._add_photos(features, slugs)
        return slugs

    def draw_maps(self, features: list[Feature], slugs: dict[str, str], host: str, rerender: bool, workers: int) -> list[Feature]:
        jobs = []
        for feature in features:
            output = self.paths.maps / f"{slugs[feature.key]}.png"
            feature.map = self.paths.relative(output)
            if rerender or not (output.exists() and answer_path(output).exists()):
                photo = self.paths.root / feature.photo.file if feature.photo else None
                jobs.append(PlaceMapJob(feature.key, feature.kind.value, mapping(feature.geometry.union_all()), host, feature.name, photo, output, answer_path(output)))
        by_key = {feature.key: feature for feature in features}
        for job, error in render_place_maps(jobs, workers):
            if error:
                logger.error("Map failed for %s: %s", job.label, error)
                by_key[job.key].map = None
        return [feature for feature in features if feature.map]

    def export(self, features: list[Feature], slugs: dict[str, str]) -> int:
        return self._export(features, slugs)


def read_documents(directory: pathlib.Path) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted((directory / "places" / "features").glob("*.json"))]

