import logging
import math
import pathlib
import re
import urllib.parse
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import geopandas as gpd
import requests
from shapely.geometry import Point

from geography import founding
from geography.data import NaturalEarth, is_younger_than
from geography.detail import (
    COUNTRIES_FILE,
    LAKES_FILE,
    LAND_MAX_AGE,
    MIN_LAKE_AREA,
    build_country_layer,
    build_lake_layer,
)
from geography.db import Database
from geography.currencies import with_main
from geography.demonyms import corrected_demonym, other_demonyms
from geography.economy import classify
from geography.flags import download_flag
from geography.government import government_form
from geography.languages import merge_languages, with_status
from geography.maps import (
    DISTRICT_TYPES,
    MAX_CITY_AREA,
    MAX_DISTRICT_AREA,
    SETTLEMENT_TYPES,
    MapJob,
    city_boundary,
    render_maps,
)
from geography.models import City, CityRole, Country, Field, ListedCapital, OfficialStatus, Provenance, Source
from geography.naming import slugify
from geography.places import CITY_ZOOM, DISTRICT_ZOOM, Nominatim
from geography.sources import cldr, geonames, iso639, iso4217, pew, restcountries, worldbank
from geography.sources.http import create_session
from geography.sources.imf import fetch_government_spending
from geography.sources.restcountries import MEMBERSHIPS, RestCountries
from geography.sources.ultimate_geography import ScopeEntry, fetch_scope
from geography.sources.wikidata import Wikidata
from geography.sources.wikipedia import ARTICLE_URL, ListedLanguage, Wikipedia

CITY_MAX_AGE = timedelta(days=30)
NOTABLE_CITY_COUNT = 5
MIN_NOTABLE_POPULATION = 10_000
CITY_STATE_AREA = 1_000
EXCLUDED_CITY_WORDS = (
    "municipality",
    "department",
    "district",
    "province",
    "governorate",
    "prefecture",
    "county",
    "canton",
    "region",
)
UNLABELLED = re.compile(r"^Q\d+$")
COASTAL_TOLERANCE = 0.2
MIN_FAME_SHARE = 0.25
CLEAR_FAME_SHARE = 0.5
FAMOUS_CANDIDATES = 10
LARGEST_CANDIDATES = 15
SUBURB_RADIUS_KM = 20
SUBURB_MIN_SITELINKS = 100
EARTH_RADIUS_KM = 6371
CAPITAL_MATCH_KM = 50
RECENT_JOIN_YEARS = 3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataPaths:
    root: pathlib.Path

    @property
    def database(self) -> pathlib.Path:
        return self.root / "geography.db"

    @property
    def countries(self) -> pathlib.Path:
        return self.root / "countries"

    @property
    def flags(self) -> pathlib.Path:
        return self.root / "flags"

    @property
    def country_maps(self) -> pathlib.Path:
        return self.root / "maps" / "countries"

    @property
    def city_maps(self) -> pathlib.Path:
        return self.root / "maps" / "cities"

    def relative(self, path: pathlib.Path) -> str:
        return path.relative_to(self.root).as_posix()


@dataclass(frozen=True)
class Options:
    refresh_cities: bool = False
    rerender_maps: bool = False
    skip_maps: bool = False
    workers: int = 4


def select_in_scope(raw_countries: list[dict], scope: list[ScopeEntry]) -> dict[str, tuple[dict, ScopeEntry]]:
    by_alpha_2 = {raw["codes"]["alpha_2"]: raw for raw in raw_countries if raw["codes"]["alpha_2"]}
    by_name = {raw["names"]["common"].casefold(): raw for raw in raw_countries}
    selected = {}
    for entry in scope:
        raw = by_alpha_2.get(entry.alpha_2) or by_name.get(entry.name.casefold())
        if raw is None:
            logger.warning("Not in restcountries: %s", entry.name)
            continue
        selected.setdefault(raw["uuid"], (raw, entry))
    return selected


def display_name(country: Country, scope_name: str) -> str:
    return country.name if country.disputed else scope_name


def file_extension(url: str) -> str:
    return pathlib.PurePosixPath(urllib.parse.urlparse(url).path).suffix.lower() or ".svg"


def normalized(name: str) -> str:
    return slugify(name).replace("_", "")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def distance_km(first: City, second: City) -> float | None:
    if None in (first.latitude, first.longitude, second.latitude, second.longitude):
        return None
    return haversine_km(first.latitude, first.longitude, second.latitude, second.longitude)


def is_famous_enough(city: City, reference: City) -> bool:
    return city.sitelinks >= MIN_FAME_SHARE * reference.sitelinks


def is_clearly_famous(city: City, reference: City) -> bool:
    return city.sitelinks >= CLEAR_FAME_SHARE * reference.sitelinks


def ranks(cities: list[City], score: Callable[[City], int | None]) -> dict[str, int]:
    ordered = sorted(cities, key=lambda city: -(score(city) or 0))
    return {city.id: position for position, city in enumerate(ordered, start=1)}


def is_suburb(city: City, bigger: City) -> bool:
    distance = distance_km(city, bigger)
    return distance is not None and distance < SUBURB_RADIUS_KM and city.sitelinks < SUBURB_MIN_SITELINKS


def is_city_state(country: Country, capital_names: list[str]) -> bool:
    if not country.area_km2 or country.area_km2 >= CITY_STATE_AREA:
        return False
    name = country.name.casefold()
    return any(capital.casefold().startswith(name) or name.startswith(capital.casefold()) for capital in capital_names)


class Collector:
    def __init__(self, database: Database, paths: DataPaths, session: requests.Session, api_key: str):
        self.database = database
        self.paths = paths
        self.session = session
        self.restcountries = RestCountries(api_key, create_session())
        self.wikidata = Wikidata(session)
        self.wikipedia = Wikipedia(session)
        self.nominatim = Nominatim()

    def run(self, options: Options) -> None:
        scope = fetch_scope(self.session)
        in_scope = select_in_scope(self.restcountries.fetch_all(), scope)
        raw_countries = [raw for raw, _ in in_scope.values()]
        qids = self._resolve_wikidata(raw_countries)
        countries = [restcountries.to_country(raw, qids[raw["uuid"]]) for raw in raw_countries if raw["uuid"] in qids]
        for country in countries:
            country.name = display_name(country, in_scope[country.restcountries_id][1].name)
        logger.info("%d countries in scope", len(countries))

        removed = self.database.remove_entities_except({country.id for country in countries})
        if removed:
            logger.info("Removed %d countries no longer in scope", len(removed))

        self._correct_demonyms(countries)
        self._add_main_currencies(countries)
        self._add_government_forms(countries)
        self._add_populations(countries)
        self._add_memberships(countries)
        self._add_languages(countries)
        self._add_religions(countries)
        self._add_founding(countries)
        self._add_economy(countries)
        self._add_flags(countries, raw_countries)
        self._remove_orphans(self.paths.flags, {country.flag for country in countries if country.flag}, "flags")
        self._add_existing_maps(countries)
        self.database.save_countries(countries)

        self._build_detail_layer(countries, options.rerender_maps)
        self._collect_cities(countries, options.refresh_cities)
        if not options.skip_maps:
            self._render_country_maps(countries, options)
            expected = {self.paths.relative(self._country_map_path(country)) for country in countries}
            self._remove_orphans(self.paths.country_maps, expected, "country maps")
            self._render_city_maps(options)

        count = self.database.export_json(self.paths.countries)
        logger.info("Exported %d countries to %s", count, self.paths.countries)

    def _resolve_wikidata(self, raw_countries: list[dict]) -> dict[str, str]:
        codes = {raw["uuid"]: raw["codes"]["alpha_2"] for raw in raw_countries if raw["codes"]["alpha_2"]}
        links = {raw["uuid"]: raw["links"]["wikipedia"] for raw in raw_countries if raw["links"].get("wikipedia")}
        by_code = self.wikidata.resolve_codes(codes)
        by_link = self.wikidata.resolve(links)

        both = by_code.keys() & by_link.keys()
        conflicts = [(by_link[key], by_code[key]) for key in both if by_link[key] != by_code[key]]
        more_specific = self.wikidata.parts_of(conflicts)
        qids = by_link | by_code
        for key in both:
            if (by_link[key], by_code[key]) in more_specific:
                qids[key] = by_link[key]
        for raw in raw_countries:
            if raw["uuid"] not in qids:
                logger.warning("No Wikidata item for %s", raw["names"]["common"])
        return qids

    def _correct_demonyms(self, countries: list[Country]) -> None:
        options = self.wikidata.demonyms({country.id for country in countries})
        for country in countries:
            corrected = corrected_demonym(country.demonym, country.name, options.get(country.id, []))
            if corrected != country.demonym:
                logger.info("Demonym for %s: %s -> %s", country.name, country.demonym, corrected)
                country.demonym = corrected
                country.demonym_female = None
            country.other_demonyms = other_demonyms(
                country.demonym, country.demonym_female, country.name, options.get(country.id, [])
            )

    def _add_main_currencies(self, countries: list[Country]) -> None:
        iso_codes = iso4217.fetch_currency_codes(self.session)
        for country in countries:
            country.currencies = with_main(country.currencies, country.alpha_2, iso_codes)

    def _add_government_forms(self, countries: list[Country]) -> None:
        missing = {country.id for country in countries if country.sovereign and not country.government}
        forms = self.wikidata.forms_of_government(missing) if missing else {}
        for country in countries:
            if country.id in forms:
                country.government = ", ".join(forms[country.id]).capitalize()
                country.provenance[Field.GOVERNMENT] = Provenance(Source.WIKIDATA)
            country.government_form = government_form(country.government) if country.sovereign else None
            country.provenance[Field.GOVERNMENT_FORM] = Provenance(Source.DERIVED)

    def _add_populations(self, countries: list[Country]) -> None:
        populations = worldbank.fetch_populations(self.session)
        for country in countries:
            if country.alpha_3 in populations:
                country.population, year = populations[country.alpha_3]
                country.provenance[Field.POPULATION] = Provenance(Source.WORLD_BANK, str(year))

    def _add_memberships(self, countries: list[Country]) -> None:
        organizations = self.wikidata.resolve({label: ARTICLE_URL + label.replace(" ", "_") for label in MEMBERSHIPS.values()})
        sovereign = {country.id for country in countries if country.sovereign}
        since = (datetime.now(UTC) - timedelta(days=365 * RECENT_JOIN_YEARS)).date().isoformat()
        joined = self.wikidata.memberships_joined_since(sovereign, organizations, since)
        for country in countries:
            listed = set(country.memberships)
            added = joined.get(country.id, set()) - listed
            if added:
                logger.info("Memberships for %s added from Wikidata: %s", country.name, ", ".join(sorted(added)))
            country.memberships = [label for label in MEMBERSHIPS.values() if label in listed | added]

    def _by_country(self, countries: list[Country], urls: list[str]) -> dict[str, list[str]]:
        qid_by_url = self.wikidata.resolve({url: url for url in set(urls)})
        found = defaultdict(list)
        for url in urls:
            if url in qid_by_url:
                found[qid_by_url[url]].append(url)

        unmatched = found.keys() - {country.id for country in countries}
        states = [country.id for country in countries if country.sovereign and country.id not in found]
        for state, whole in self.wikidata.parts_of([(state, whole) for state in states for whole in unmatched]):
            found[state].extend(found[whole])
        return found

    def _add_languages(self, countries: list[Country]) -> None:
        codes = iso639.fetch_codes(self.session)
        use = cldr.fetch_language_use(self.session, codes)
        official = self.wikidata.official_languages({country.id for country in countries})
        statuses = {status.country_url: status for status in self.wikipedia.language_statuses()}
        urls = self._by_country(countries, list(statuses))
        for country in countries:
            spoken = use.territories.get(country.alpha_2 or "", [])
            languages = merge_languages(official.get(country.id, []), spoken, codes)
            source = Source.WIKIDATA
            if country.id in urls:
                status = statuses[urls[country.id][0]]
                national = status.national
                if status.incomplete:
                    national = national + [ListedLanguage(language.name) for language in languages if language.official is OfficialStatus.NATIONAL]
                languages = with_status(languages, national, status.regional)
                source = Source.WIKIPEDIA
            country.languages = languages
            country.provenance[Field.OFFICIAL_LANGUAGES] = Provenance(source)
            country.provenance[Field.SPOKEN_LANGUAGES] = Provenance(Source.CLDR, f"CLDR {use.version}")

    def _add_religions(self, countries: list[Country]) -> None:
        data = pew.fetch_religions(self.session)
        listed = {record.country_url: record.religion for record in self.wikipedia.state_religions()}
        urls = self._by_country(countries, list(listed))
        wikidata = self.wikidata.official_religions({country.id for country in countries})
        for country in countries:
            country.religions = data.find(country.ccn3, country.name)
            country.official_religions = sorted({listed[url] for url in urls.get(country.id, [])})
            if bool(country.official_religions) != bool(wikidata.get(country.id)):
                logger.info(
                    "State religion disagreement for %s: Wikipedia %s, Wikidata %s",
                    country.name,
                    country.official_religions,
                    wikidata.get(country.id, []),
                )
            country.provenance[Field.RELIGIONS] = Provenance(Source.PEW, str(data.year))
            country.provenance[Field.OFFICIAL_RELIGIONS] = Provenance(Source.WIKIPEDIA)

    def _add_founding(self, countries: list[Country]) -> None:
        formation = {record.country_url: record for record in self.wikipedia.formation()}
        independence_days = defaultdict(list)
        for record in self.wikipedia.independence_days():
            independence_days[record.country_url].append(record)
        formation_urls = self._by_country(countries, list(formation))
        independence_urls = self._by_country(countries, list(independence_days))
        inceptions = self.wikidata.inceptions({country.id for country in countries})
        for country in countries:
            record = formation[formation_urls[country.id][0]] if country.id in formation_urls else None
            country_inceptions = inceptions.get(country.id, [])
            records = [day for url in independence_urls.get(country.id, []) for day in independence_days[url]]
            country.established = founding.established(record, country_inceptions)
            country.independence = founding.independence(records, record, country_inceptions)
            country.provenance[Field.ESTABLISHED] = Provenance(Source.DERIVED)
            country.provenance[Field.INDEPENDENCE] = Provenance(Source.WIKIPEDIA)

    def _add_economy(self, countries: list[Country]) -> None:
        income_groups = worldbank.fetch_income_groups(self.session)
        military = worldbank.fetch_military_spending(self.session)
        spending = fetch_government_spending(self.session)
        communist_urls = self.wikipedia.communist_states()
        communist = set(self.wikidata.resolve({url: url for url in communist_urls}).values())
        for country in countries:
            country_spending = spending.find(country.alpha_3, country.name)
            country_military = military.get(country.alpha_3 or "")
            country.economy = classify(
                income_groups.get(country.alpha_2 or ""),
                country_spending.percent_of_gdp if country_spending else None,
                country_military[0] if country_military else None,
                country.id in communist,
            )
            country.provenance[Field.INCOME_GROUP] = Provenance(Source.WORLD_BANK)
            country.provenance[Field.MILITARY_SPENDING] = Provenance(
                Source.WORLD_BANK,
                f"SIPRI via World Bank, {country_military[1]}" if country_military else None,
            )
            country.provenance[Field.GOVERNMENT_SPENDING] = Provenance(
                Source.IMF,
                f"{spending.edition}, {country_spending.year}" if country_spending else spending.edition,
            )
            country.provenance[Field.ECONOMIC_SYSTEM] = Provenance(Source.DERIVED)

    def _add_flags(self, countries: list[Country], raw_countries: list[dict]) -> None:
        raw_by_id = {raw["uuid"]: raw for raw in raw_countries}
        fallback_urls = self.wikidata.flag_urls({country.id for country in countries})
        changed = 0
        for country in countries:
            url, source = restcountries.flag_url(raw_by_id[country.restcountries_id]), Source.RESTCOUNTRIES
            if url is None:
                url, source = fallback_urls.get(country.id), Source.WIKIDATA
            if url is None:
                logger.warning("No flag for %s", country.name)
                continue
            path = self.paths.flags / f"{slugify(country.name)}{file_extension(url)}"
            try:
                changed += download_flag(self.session, url, path)
            except requests.RequestException:
                logger.exception("Flag download failed for %s", country.name)
                continue
            country.flag = self.paths.relative(path)
            country.provenance[Field.FLAG] = Provenance(source)
        logger.info("Flags: %d changed", changed)

    def _add_existing_maps(self, countries: list[Country]) -> None:
        for country in countries:
            path = self._country_map_path(country)
            if path.exists():
                country.map = self.paths.relative(path)
                country.provenance[Field.MAP] = Provenance(Source.NATURAL_EARTH)

    def _country_map_path(self, country: Country) -> pathlib.Path:
        return self.paths.country_maps / f"{slugify(country.name)}.png"

    def _collect_cities(self, countries: list[Country], refresh: bool) -> None:
        qids = {country.id for country in countries}
        wikidata_provenance = Provenance(Source.WIKIDATA)
        capitals = self._resolve_capitals(countries)
        for country in countries:
            self.database.link_cities(
                country.id, CityRole.CAPITAL, capitals.get(country.id, []), Provenance(Source.RESTCOUNTRIES)
            )

        cutoff = (datetime.now(UTC) - CITY_MAX_AGE).date()
        largest = self._largest_cities(countries)
        candidates = {}
        for country in countries:
            retrieved = self.database.retrieved(country.id, Field.NOTABLE_CITIES)
            if not refresh and retrieved and retrieved > cutoff:
                continue
            logger.info("Ranking cities for %s", country.name)
            try:
                famous = self.wikidata.ranked_cities(country.id, qids, FAMOUS_CANDIDATES)
                candidates[country.id] = list(dict.fromkeys(famous + largest.get(country.id, [])))
            except requests.RequestException:
                logger.exception("City ranking failed for %s", country.name)

        wanted = self.database.linked_city_ids() | {city for found in candidates.values() for city in found}
        details = self.wikidata.city_details(wanted)
        shapes = self._border_shapes(countries) if candidates else {}
        for country in countries:
            if country.id in candidates:
                notable = self._select_notable(country, capitals.get(country.id, []), candidates[country.id], details, shapes)
                self.database.link_cities(country.id, CityRole.NOTABLE, notable, wikidata_provenance)

        linked = self.database.linked_city_ids()
        self.database.save_cities([city for city_id, city in details.items() if city_id in linked])
        logger.info("Cities: %d stored", len(linked))

    def _largest_cities(self, countries: list[Country]) -> dict[str, list[str]]:
        by_code = geonames.fetch_largest_cities(self.session, LARGEST_CANDIDATES)
        cities = {country.id: by_code.get(country.alpha_2 or "", []) for country in countries}
        qids = self.wikidata.by_geonames_ids({city.geonames_id for found in cities.values() for city in found})
        return {
            country_id: [qids[city.geonames_id] for city in found if city.geonames_id in qids]
            for country_id, found in cities.items()
        }

    def _resolve_capitals(self, countries: list[Country]) -> dict[str, list[str]]:
        listed = self.wikidata.capitals({country.id for country in countries})
        names = {city.id: city.name for city in self.wikidata.city_details(set().union(*listed.values())).values()}
        resolved = {}
        for country in countries:
            capital_names = [capital.name for capital in country.listed_capitals]
            candidates = {normalized(names[city_id]): city_id for city_id in listed.get(country.id, []) if city_id in names}
            found = {name: candidates[normalized(name)] for name in capital_names if normalized(name) in candidates}
            try:
                missing = [name for name in capital_names if name not in found]
                if missing:
                    found |= self.wikidata.cities_named(country.id, missing)
                missing = [capital for capital in country.listed_capitals if capital.name not in found]
                if missing:
                    found |= self._nearest_named(missing)
            except requests.RequestException:
                logger.exception("Capital lookup failed for %s", country.name)
            for name in capital_names:
                if name not in found:
                    logger.warning("Capital %s of %s not found in Wikidata", name, country.name)
            chosen = [found[name] for name in capital_names if name in found]
            resolved[country.id] = list(dict.fromkeys(chosen)) or listed.get(country.id, [])
        return resolved

    def _nearest_named(self, capitals: list[ListedCapital]) -> dict[str, str]:
        places = self.wikidata.settlements_named([capital.name for capital in capitals])
        found = {}
        for capital in capitals:
            if capital.latitude is None:
                continue
            distances = [
                (haversine_km(capital.latitude, capital.longitude, place.latitude, place.longitude), place.qid)
                for place in places
                if place.name == capital.name
            ]
            nearest = min(distances, default=None)
            if nearest and nearest[0] <= CAPITAL_MATCH_KM:
                found[capital.name] = nearest[1]
        return found

    def _border_shapes(self, countries: list[Country]) -> dict:
        shapes = self._natural_earth_shapes(countries)
        detail = gpd.read_file(COUNTRIES_FILE).set_index("id").geometry
        shapes.update(detail.to_dict())
        return shapes

    def _natural_earth_shapes(self, countries: list[Country]) -> dict:
        data = NaturalEarth.load()
        shapes = {}
        for country in countries:
            shape = data.shape_for(country.name, country.alpha_3)
            if shape is not None:
                shapes[country.id] = shape.core.geometry.union_all()
        return shapes

    def _select_notable(
        self,
        country: Country,
        capital_ids: list[str],
        candidate_ids: list[str],
        details: dict[str, City],
        shapes: dict,
    ) -> list[str]:
        capitals = [details[city_id] for city_id in capital_ids if city_id in details]
        capital_names = [capital.name for capital in capitals]
        if is_city_state(country, capital_names):
            return []
        candidates = [details[city_id] for city_id in candidate_ids if city_id in details]
        known = capitals + candidates
        reference = max(known, key=lambda city: city.sitelinks, default=None)
        fame_rank = ranks(known, lambda city: city.sitelinks)
        size_rank = ranks(known, lambda city: city.population)
        ordered = sorted(candidates, key=lambda city: (fame_rank[city.id] + size_rank[city.id], fame_rank[city.id]))

        seen = {name.casefold() for name in capital_names}
        selected = []
        for city in ordered:
            if city.id in capital_ids or city.name.casefold() in seen:
                continue
            if UNLABELLED.match(city.name) or not slugify(city.name):
                continue
            if any(word in city.name.casefold() for word in EXCLUDED_CITY_WORDS):
                continue
            if city.population is not None and city.population < MIN_NOTABLE_POPULATION:
                continue
            if self._inside_other_country(city, country.id, shapes):
                continue
            if not is_famous_enough(city, reference):
                continue
            if size_rank[city.id] > LARGEST_CANDIDATES and not is_clearly_famous(city, reference):
                continue
            if any(is_suburb(city, bigger) for bigger in capitals + [details[chosen] for chosen in selected]):
                continue
            selected.append(city.id)
            seen.add(city.name.casefold())
            if len(selected) == NOTABLE_CITY_COUNT:
                break
        return selected

    def _inside_other_country(self, city: City, country_id: str, shapes: dict) -> bool:
        if city.latitude is None:
            return False
        point = Point(city.longitude, city.latitude)
        containing = [other_id for other_id, shape in shapes.items() if shape.contains(point)]
        if containing:
            return min(containing, key=lambda other_id: shapes[other_id].area) != country_id
        distances = {other_id: shape.distance(point) for other_id, shape in shapes.items()}
        nearest = min(distances, key=distances.get)
        return nearest != country_id and distances[nearest] <= COASTAL_TOLERANCE

    def _build_detail_layer(self, countries: list[Country], rebuild: bool) -> None:
        built = [COUNTRIES_FILE, LAKES_FILE]
        if not rebuild and all(path.exists() and is_younger_than(path, LAND_MAX_AGE) for path in built):
            return
        relations = self.wikidata.osm_relations({country.id for country in countries})
        features = self.nominatim.relations(sorted(set(relations.values())))
        boundaries = {country_id: features[relation] for country_id, relation in relations.items() if relation in features}
        shapes = self._natural_earth_shapes([country for country in countries if country.id not in boundaries])
        count = build_country_layer(boundaries, shapes)
        lakes = self.nominatim.relations(self.wikidata.large_lakes(MIN_LAKE_AREA))
        lake_count = build_lake_layer(lakes)
        logger.info("Detail layer: %d countries, %d lakes", count, lake_count)

    def _render_country_maps(self, countries: list[Country], options: Options) -> None:
        jobs = [
            MapJob(country.id, country.id, self._country_map_path(country), country.name, country.alpha_3)
            for country in countries
            if options.rerender_maps or not self._country_map_path(country).exists()
        ]
        provenance = Provenance(Source.NATURAL_EARTH)
        for job, error in render_maps(jobs, options.workers):
            if error:
                logger.error("Map failed for %s: %s", job.entity_name, error)
            else:
                self.database.set_entity_map(job.key, self.paths.relative(job.output), provenance)

    def _city_map_path(self, city) -> pathlib.Path:
        return self.paths.city_maps / f"{slugify(city['entity_name'])}__{slugify(city['name'])}.png"

    def _render_city_maps(self, options: Options) -> None:
        cities = [
            city
            for city in self.database.city_map_targets()
            if options.rerender_maps
            or city["map"] != self.paths.relative(self._city_map_path(city))
            or not (self.paths.root / city["map"]).exists()
        ]
        polygons = self.nominatim.relations([city["osm_relation"] for city in cities if city["osm_relation"]])

        jobs, sources = [], defaultdict(int)
        for city in cities:
            output = self._city_map_path(city)
            point = (city["longitude"], city["latitude"]) if city["latitude"] is not None else None
            geometry, source = self._city_boundary(city, polygons, point)
            if geometry is None and point is None:
                logger.warning("No location for %s", city["name"])
                continue
            sources[source] += 1
            jobs.append(
                MapJob(city["id"], city["entity_id"], output, city["entity_name"], city["alpha_3"], city["name"], geometry, point)
            )
        logger.info("City shapes: %s", dict(sources))

        for job, error in render_maps(jobs, options.workers):
            if error:
                logger.error("Map failed for %s: %s", job.place_name, error)
            else:
                self.database.set_city_map(job.key, self.paths.relative(job.output))
        referenced = {row["map"] for row in self.database.city_map_targets() if row["map"]}
        self._remove_orphans(self.paths.city_maps, referenced, "city maps")

    def _remove_orphans(self, directory: pathlib.Path, referenced: set[str], kind: str) -> None:
        orphans = [path for path in directory.glob("*") if path.is_file() and self.paths.relative(path) not in referenced]
        for path in orphans:
            path.unlink()
        if orphans:
            logger.info("Removed %d orphaned %s", len(orphans), kind)

    def _city_boundary(
        self,
        city,
        polygons: dict[str, dict],
        point: tuple[float, float] | None,
    ) -> tuple[dict | None, str]:
        if city["osm_relation"]:
            geometry = city_boundary(polygons.get(city["osm_relation"]), point)
            if geometry:
                return geometry, "wikidata relation"
        if point is None:
            return None, "none"
        attempts = (
            (CITY_ZOOM, SETTLEMENT_TYPES, MAX_CITY_AREA, "reverse lookup"),
            (DISTRICT_ZOOM, DISTRICT_TYPES, MAX_DISTRICT_AREA, "district lookup"),
        )
        longitude, latitude = point
        for zoom, types, max_area, source in attempts:
            try:
                feature = self.nominatim.reverse(latitude, longitude, zoom)
            except requests.RequestException:
                logger.exception("Reverse lookup failed for %s", city["name"])
                continue
            if feature and feature["properties"].get("addresstype") in types:
                geometry = city_boundary(feature, point, max_area)
                if geometry:
                    return geometry, source
        return None, "point"
