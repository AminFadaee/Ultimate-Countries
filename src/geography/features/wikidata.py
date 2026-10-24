import itertools
from collections import defaultdict
from dataclasses import dataclass, field

from geography.sources.wikidata import (
    POINT,
    Wikidata,
    display_name,
    qid_of,
    values_clause,
)

BATCH = 200
CITY = "Q515"
WORLD_HERITAGE_SITE = "Q9259"
METRES_PER_KILOMETRE = 1000
SQUARE_METRES_PER_SQUARE_KILOMETRE = 1_000_000
ANCIENT_CITY = "Q15661340"
ARCHAEOLOGICAL_SITE = "Q839954"
RANKS = {"PreferredRank": 2, "NormalRank": 1}
QUANTITIES = (
    ("height_m", "height", 1),
    ("elevation_m", "elevation", 1),
    ("length_km", "length", METRES_PER_KILOMETRE),
    ("area_km2", "area", SQUARE_METRES_PER_SQUARE_KILOMETRE),
)


@dataclass
class ItemFacts:
    label: str
    sitelinks: int = 0
    images: list[str] = field(default_factory=list)
    coordinates: tuple[float, float] | None = None
    wikipedia: str | None = None
    height_m: float | None = None
    elevation_m: float | None = None
    length_km: float | None = None
    area_km2: float | None = None
    world_heritage: bool = False
    best: dict[str, tuple[int, float]] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class SeaItem:
    qid: str
    label: str
    sitelinks: int
    parents: tuple[str, ...]


def rank_of(row: dict[str, str], key: str) -> int:
    return RANKS.get(row.get(f"{key}Rank", "").rsplit("#", 1)[-1], 0)


class FeatureQueries:
    def __init__(self, wikidata: Wikidata):
        self.wikidata = wikidata

    def ranked(self, classes: tuple[str, ...], min_sitelinks: int) -> list[str]:
        links: dict[str, int] = {}
        for batch in itertools.batched(sorted(self.with_subclasses(classes)), BATCH, strict=False):
            rows = self.wikidata._select(f"""
                SELECT DISTINCT ?item ?links WHERE {{
                  VALUES ?type {{ {values_clause(batch)} }}
                  ?item wdt:P31 ?type ; wikibase:sitelinks ?links .
                  FILTER(?links >= {min_sitelinks})
                }}""")
            links.update((qid_of(row["item"]), int(row["links"])) for row in rows)
        return sorted(links, key=lambda qid: -links[qid])

    def with_subclasses(self, classes: tuple[str, ...]) -> set[str]:
        rows = self.wikidata._select(f"""
            SELECT DISTINCT ?type WHERE {{
              VALUES ?class {{ {values_clause(classes)} }}
              ?type wdt:P279 ?class .
            }}""")
        return set(classes) | {qid_of(row["type"]) for row in rows}

    def ranked_heritage(self, min_sitelinks: int) -> list[str]:
        rows = self.wikidata._select(f"""
            SELECT DISTINCT ?item ?links WHERE {{
              ?item wdt:P1435 wd:{WORLD_HERITAGE_SITE} ; wikibase:sitelinks ?links .
              FILTER(?links >= {min_sitelinks})
            }} ORDER BY DESC(?links)""")
        return list(dict.fromkeys(qid_of(row["item"]) for row in rows))

    def facts(self, qids: set[str]) -> dict[str, ItemFacts]:
        found: dict[str, ItemFacts] = {}
        for batch in itertools.batched(sorted(qids), BATCH, strict=False):
            rows = self.wikidata._select(f"""
                SELECT ?item ?itemLabel ?links ?image ?imageRank ?coordinates ?article ?height ?heightRank ?elevation ?elevationRank
                       ?length ?lengthRank ?area ?areaRank ?heritage WHERE {{
                  VALUES ?item {{ {values_clause(batch)} }}
                  ?item wikibase:sitelinks ?links .
                  OPTIONAL {{ ?item p:P18 ?imageStatement . ?imageStatement ps:P18 ?image ; wikibase:rank ?imageRank }}
                  OPTIONAL {{ ?item wdt:P625 ?coordinates }}
                  OPTIONAL {{ ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> }}
                  OPTIONAL {{ ?item p:P2048 ?heightStatement . ?heightStatement psn:P2048/wikibase:quantityAmount ?height ; wikibase:rank ?heightRank }}
                  OPTIONAL {{ ?item p:P2044 ?elevationStatement . ?elevationStatement psn:P2044/wikibase:quantityAmount ?elevation ; wikibase:rank ?elevationRank }}
                  OPTIONAL {{ ?item p:P2043 ?lengthStatement . ?lengthStatement psn:P2043/wikibase:quantityAmount ?length ; wikibase:rank ?lengthRank }}
                  OPTIONAL {{ ?item p:P2046 ?areaStatement . ?areaStatement psn:P2046/wikibase:quantityAmount ?area ; wikibase:rank ?areaRank }}
                  OPTIONAL {{ ?item wdt:P1435 ?heritage . FILTER(?heritage = wd:{WORLD_HERITAGE_SITE}) }}
                  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul" }}
                }}""")
            for row in rows:
                self._merge(found, row)
        return found

    def _merge(self, found: dict[str, ItemFacts], row: dict[str, str]) -> None:
        qid = qid_of(row["item"])
        facts = found.setdefault(qid, ItemFacts(display_name(row["itemLabel"]), int(row["links"])))
        if (image := row.get("image")) and image not in facts.images and rank_of(row, "image"):
            facts.images.insert(0 if rank_of(row, "image") == RANKS["PreferredRank"] else len(facts.images), image)
        facts.wikipedia = facts.wikipedia or row.get("article")
        if facts.coordinates is None and (point := POINT.match(row.get("coordinates", ""))):
            facts.coordinates = (float(point.group(1)), float(point.group(2)))
        for attribute, key, divisor in QUANTITIES:
            if key in row and (rank := rank_of(row, key)):
                candidate = (rank, float(row[key]) / divisor)
                if candidate > facts.best.get(attribute, (0, 0.0)):
                    facts.best[attribute] = candidate
                    setattr(facts, attribute, candidate[1])
        facts.world_heritage = facts.world_heritage or "heritage" in row

    def instances_of(self, qids: set[str], cls: str) -> set[str]:
        return self._matching(qids, f"?item wdt:P31 ?type . ?type wdt:P279* wd:{cls} .")

    def typed_as_site(self, qids: set[str]) -> set[str]:
        return self._matching(qids, f"?item wdt:P31 ?type . ?type wdt:P279* wd:{ARCHAEOLOGICAL_SITE} . FILTER(?type != wd:{ANCIENT_CITY})")

    def part_labels(self, qids: set[str]) -> dict[str, list[str]]:
        found: dict[str, list[str]] = defaultdict(list)
        for batch in itertools.batched(sorted(qids), BATCH, strict=False):
            rows = self.wikidata._select(f"""
                SELECT ?item ?label WHERE {{
                  VALUES ?item {{ {values_clause(batch)} }}
                  ?item wdt:P527 ?part . ?part rdfs:label ?label . FILTER(lang(?label) = "en")
                }}""")
            for row in rows:
                found[qid_of(row["item"])].append(row["label"])
        return dict(found)

    def parts_of(self, qids: set[str]) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for batch in itertools.batched(sorted(qids), BATCH, strict=False):
            rows = self.wikidata._select(f"""
                SELECT ?part ?whole WHERE {{
                  VALUES ?part {{ {values_clause(batch)} }}
                  VALUES ?whole {{ {values_clause(sorted(qids))} }}
                  ?part wdt:P361 ?whole . FILTER(?part != ?whole)
                }}""")
            pairs.update((qid_of(row["part"]), qid_of(row["whole"])) for row in rows)
        return pairs

    def countries(self, qids: set[str]) -> dict[str, list[str]]:
        found: dict[str, list[str]] = defaultdict(list)
        for batch in itertools.batched(sorted(qids), BATCH, strict=False):
            rows = self.wikidata._select(f"""
                SELECT ?item ?country WHERE {{
                  VALUES ?item {{ {values_clause(batch)} }}
                  ?item p:P17 ?statement . ?statement ps:P17 ?country ; wikibase:rank ?rank .
                  FILTER(?rank != wikibase:DeprecatedRank)
                }}""")
            for row in rows:
                found[qid_of(row["item"])].append(qid_of(row["country"]))
        return {qid: list(dict.fromkeys(countries)) for qid, countries in found.items()}

    def inhabited(self, qids: set[str]) -> set[str]:
        return self._matching(qids, "?item wdt:P1082 ?population . FILTER(?population > 0) FILTER NOT EXISTS { ?item wdt:P576 ?dissolved }")

    def _matching(self, qids: set[str], condition: str) -> set[str]:
        found = set()
        for batch in itertools.batched(sorted(qids), BATCH, strict=False):
            rows = self.wikidata._select(f"""
                SELECT DISTINCT ?item WHERE {{
                  VALUES ?item {{ {values_clause(batch)} }}
                  {condition}
                }}""")
            found.update(qid_of(row["item"]) for row in rows)
        return found

    def cities(self, qids: set[str]) -> dict[str, list[str]]:
        places: dict[str, dict[str, str]] = defaultdict(dict)
        for batch in itertools.batched(sorted(qids), BATCH, strict=False):
            rows = self.wikidata._select(f"""
                SELECT ?item ?near ?nearLabel ?far ?farLabel WHERE {{
                  VALUES ?item {{ {values_clause(batch)} }}
                  ?item wdt:P131 ?near .
                  OPTIONAL {{ ?near wdt:P131 ?far }}
                  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul" }}
                }}""")
            for row in rows:
                places[qid_of(row["item"])][qid_of(row["near"])] = row["nearLabel"]
                if "far" in row:
                    places[qid_of(row["item"])][qid_of(row["far"])] = row["farLabel"]
        city_ids = self.instances_of({place for found in places.values() for place in found}, CITY)
        return {
            qid: sorted({label for place, label in found.items() if place in city_ids})
            for qid, found in places.items()
        }

    def sea_items(self, mrgids: set[str]) -> dict[str, SeaItem]:
        rows = self.wikidata._select(f"""
            SELECT ?mrgid ?item ?itemLabel ?links ?parent WHERE {{
              VALUES ?mrgid {{ {" ".join(f'"{mrgid}"' for mrgid in sorted(mrgids))} }}
              ?item wdt:P3006 ?mrgid ; wikibase:sitelinks ?links .
              OPTIONAL {{ ?item wdt:P361 ?parent }}
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul" }}
            }}""")
        parents: dict[str, set[str]] = defaultdict(set)
        items = {}
        for row in rows:
            if "parent" in row:
                parents[row["mrgid"]].add(qid_of(row["parent"]))
            items[row["mrgid"]] = (qid_of(row["item"]), display_name(row["itemLabel"]), int(row["links"]))
        return {
            mrgid: SeaItem(qid, label, links, tuple(sorted(parents[mrgid])))
            for mrgid, (qid, label, links) in items.items()
        }
