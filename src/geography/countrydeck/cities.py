import itertools
import math
from dataclasses import asdict, dataclass

import geopandas as gpd
from shapely.geometry import Point

from geography.countrydeck.names import POINT, named
from geography.countrydeck.provinces import mainland_units
from geography.data import NaturalEarth
from geography.features.wikidata import FeatureQueries
from geography.render import (
    EQUAL_AREA,
    NEARBY_PART_DISTANCE,
    get_mainland,
    local_projection,
)
from geography.sources.wikidata import DETAIL_BATCH, Wikidata, qid_of, values_clause

POOL_POPULATION = 40_000
ACCEPTED = ("Q515", "Q3957", "Q15284", "Q7930989")
REJECTED = ("Q1907114", "Q159313", "Q6602", "Q149621", "Q23442")
BIG_POPULATION, BIG_LINKS = 100_000, 40
SMALL_POPULATION, SMALL_LINKS = 60_000, 65
SATELLITE_RATIO = 3
SATELLITE_KM = 0.45
FAMOUS_LINKS = 50
EARTH_RADIUS_KM = 6371


@dataclass
class City:
    id: str
    name: str
    native: str | None
    longitude: float
    latitude: float
    population: int
    links: int
    centre: bool
    province: str | None = None


def distance_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    (lat1, lon1), (lat2, lon2) = ((math.radians(a), math.radians(b)) for a, b in (first, second))
    haversine = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(haversine))


def pool_of(wikidata: Wikidata, country_qid: str) -> dict[str, dict]:
    rows = wikidata._select(f"""
        SELECT DISTINCT ?c ?links ?co WHERE {{
          ?c wdt:P17 wd:{country_qid} ; wdt:P1082 ?p ; wikibase:sitelinks ?links ; wdt:P625 ?co . FILTER(?p > {POOL_POPULATION})
        }}""")
    return {qid_of(row["c"]): {"links": int(row["links"]), "point": row["co"]} for row in rows}


def settlements(queries: FeatureQueries, pool: set[str], centres: set[str]) -> set[str]:
    accepted = set().union(*(queries.instances_of(pool, cls) for cls in ACCEPTED)) | centres
    rejected = set().union(*(queries.instances_of(accepted, cls) for cls in REJECTED))
    return accepted - (rejected - centres)


def merged_units(wikidata: Wikidata, qids: set[str], names: dict) -> set[str]:
    capitals: dict[str, set[str]] = {}
    for batch in itertools.batched(sorted(qids), DETAIL_BATCH, strict=False):
        rows = wikidata._select(f"""
            SELECT ?c ?label WHERE {{ VALUES ?c {{ {values_clause(batch)} }}
              ?c wdt:P36 ?capital . ?capital rdfs:label ?label . FILTER(lang(?label) = "en") }}""")
        for row in rows:
            capitals.setdefault(qid_of(row["c"]), set()).add(row["label"].casefold())
    return {qid for qid, labels in capitals.items() if names[qid].name.casefold() not in labels}


def is_satellite(city: City, bigger: list[City]) -> bool:
    for other in bigger:
        if other.population >= SATELLITE_RATIO * city.population and distance_km((city.latitude, city.longitude), (other.latitude, other.longitude)) <= SATELLITE_KM * math.sqrt(other.population / 1000):
            return True
    return False


def wanted(city: City, bigger: list[City]) -> bool:
    if city.centre:
        return True
    if is_satellite(city, bigger) and city.links < FAMOUS_LINKS:
        return False
    return city.links >= BIG_LINKS and city.population >= BIG_POPULATION or city.links >= SMALL_LINKS and city.population >= SMALL_POPULATION


def near_mainland(data: NaturalEarth, code: str, cities: list[City]) -> list[City]:
    mainland = get_mainland(data.country(code).core.geometry)
    local = local_projection(mainland)
    reach = mainland.to_crs(local).iloc[0]
    points = gpd.GeoSeries([Point(city.longitude, city.latitude) for city in cities], crs="EPSG:4326").to_crs(local)
    return [city for city, near in zip(cities, points.distance(reach) <= NEARBY_PART_DISTANCE, strict=True) if near]


def assign_provinces(data: NaturalEarth, code: str, cities: list[City]) -> None:
    units = mainland_units(data, code).to_crs(EQUAL_AREA)
    for city in cities:
        point = gpd.GeoSeries([Point(city.longitude, city.latitude)], crs="EPSG:4326").to_crs(EQUAL_AREA).iloc[0]
        inside = units[units.geometry.contains(point)]
        city.province = (inside if not inside.empty else units.iloc[[units.geometry.distance(point).argmin()]])["wikidataid"].iloc[0]


def collect_cities(wikidata: Wikidata, data: NaturalEarth, code: str, country_qid: str, language: str, provinces: list[dict]) -> list[dict]:
    queries = FeatureQueries(wikidata)
    centres = {province["centre"]["id"] for province in provinces if province["centre"]}
    pool = pool_of(wikidata, country_qid)
    for centre in (province["centre"] for province in provinces if province["centre"]):
        pool.setdefault(centre["id"], {"links": 0, "point": None})
    chosen = settlements(queries, set(pool), centres)
    links = wikidata.sitelink_counts(chosen)
    populations = {}
    for batch in itertools.batched(sorted(chosen), DETAIL_BATCH, strict=False):
        populations.update(wikidata._populations(batch))
    names = named(wikidata, chosen, language)
    chosen -= merged_units(wikidata, chosen - centres, names)
    centre_points = {province["centre"]["id"]: (province["centre"]["longitude"], province["centre"]["latitude"]) for province in provinces if province["centre"]}
    cities = []
    for qid in chosen:
        match = POINT.match(pool[qid]["point"] or "")
        position = (float(match.group(1)), float(match.group(2))) if match else centre_points.get(qid)
        if position is None or not (populations.get(qid) or qid in centres):
            continue
        cities.append(City(qid, names[qid].name, names[qid].native, position[0], position[1], populations.get(qid, 0), links.get(qid, pool[qid]["links"]), qid in centres))
    best: dict[str, City] = {}
    for city in cities:
        key = city.name.casefold()
        if key not in best or (city.centre, city.links) > (best[key].centre, best[key].links):
            best[key] = city
    ranked = sorted(best.values(), key=lambda city: -city.population)
    kept = [city for index, city in enumerate(ranked) if wanted(city, ranked[:index])]
    kept = near_mainland(data, code, kept)
    assign_provinces(data, code, kept)
    return [asdict(city) for city in kept]
