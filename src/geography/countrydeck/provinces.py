import itertools
from dataclasses import asdict, dataclass

import geopandas as gpd

from geography.countrydeck.names import POINT, named
from geography.data import NaturalEarth
from geography.render import NEARBY_PART_DISTANCE, get_mainland, local_projection
from geography.sources.wikidata import DETAIL_BATCH, Wikidata, qid_of, values_clause

SQUARE_METRES_PER_SQUARE_KILOMETRE = 1_000_000


@dataclass
class Centre:
    id: str
    name: str
    native: str | None
    longitude: float | None
    latitude: float | None


@dataclass
class Province:
    id: str
    name: str
    native: str | None
    centre: Centre | None
    population: int | None
    area_km2: float | None


def mainland_units(data: NaturalEarth, code: str, include_overseas: bool = False) -> gpd.GeoDataFrame:
    units = data.regions[(data.regions["adm0_a3"] == code) & data.regions["wikidataid"].notna()]
    if include_overseas:
        return units
    mainland = get_mainland(data.country(code).core.geometry)
    local = local_projection(mainland)
    reach = mainland.to_crs(local).iloc[0]
    near = units.geometry.to_crs(local).distance(reach) <= NEARBY_PART_DISTANCE
    return units[near.to_numpy()]


def centres_of(wikidata: Wikidata, provinces: set[str], language: str) -> dict[str, Centre]:
    chosen: dict[str, tuple[bool, str, str | None]] = {}
    for batch in itertools.batched(sorted(provinces), DETAIL_BATCH, strict=False):
        rows = wikidata._select(f"""
            SELECT ?i ?c ?rank ?co WHERE {{
              VALUES ?i {{ {values_clause(batch)} }}
              ?i p:P36 ?statement . ?statement ps:P36 ?c ; wikibase:rank ?rank .
              FILTER(?rank != wikibase:DeprecatedRank)
              FILTER NOT EXISTS {{ ?statement pq:P582 ?end }}
              OPTIONAL {{ ?c wdt:P625 ?co }}
            }}""")
        for row in rows:
            province, city = qid_of(row["i"]), qid_of(row["c"])
            key = (row["rank"].endswith("PreferredRank"), city, row.get("co"))
            if province not in chosen or key > chosen[province]:
                chosen[province] = key
    names = named(wikidata, {city for _, city, _ in chosen.values()}, language)
    centres = {}
    for province, (_, city, point) in chosen.items():
        match = POINT.match(point or "")
        label = names.get(city)
        centres[province] = Centre(city, label.name if label else city, label.native if label else None,
                                   float(match.group(1)) if match else None, float(match.group(2)) if match else None)
    return centres


def areas_of(wikidata: Wikidata, ids: set[str]) -> dict[str, float]:
    rows = wikidata._select(f"""
        SELECT ?i (MAX(?area) AS ?largest) WHERE {{
          VALUES ?i {{ {values_clause(sorted(ids))} }}
          ?i p:P2046 ?statement . ?statement psn:P2046/wikibase:quantityAmount ?area ; wikibase:rank ?rank .
          FILTER(?rank != wikibase:DeprecatedRank)
        }} GROUP BY ?i""")
    return {qid_of(row["i"]): float(row["largest"]) / SQUARE_METRES_PER_SQUARE_KILOMETRE for row in rows}


def collect_provinces(wikidata: Wikidata, data: NaturalEarth, code: str, language: str, include_overseas: bool = False) -> list[dict]:
    units = mainland_units(data, code, include_overseas)
    ids = set(units["wikidataid"])
    names = named(wikidata, ids, language, strip_type=True)
    centres = centres_of(wikidata, ids, language)
    populations = wikidata.populations(ids)
    areas = areas_of(wikidata, ids)
    provinces = [
        Province(qid, names[qid].name, names[qid].native, centres.get(qid), populations.get(qid), areas.get(qid))
        for qid in sorted(ids, key=lambda qid: names[qid].name)
    ]
    return [asdict(province) for province in provinces]

