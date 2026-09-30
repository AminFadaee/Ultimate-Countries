import itertools
from collections import defaultdict
from dataclasses import dataclass

from geography.sources.wikidata import POINT, Wikidata, display_name, qid_of, values_clause

BATCH = 200
CITY = "Q515"
WORLD_HERITAGE_SITE = "Q9259"
METRES_PER_KILOMETRE = 1000
SQUARE_METRES_PER_SQUARE_KILOMETRE = 1_000_000


@dataclass
class ItemFacts:
    label: str
    sitelinks: int = 0
    image: str | None = None
    coordinates: tuple[float, float] | None = None
    wikipedia: str | None = None
    height_m: float | None = None
    elevation_m: float | None = None
    length_km: float | None = None
    area_km2: float | None = None
    world_heritage: bool = False


@dataclass(frozen=True)
class SeaItem:
    qid: str
    label: str
    sitelinks: int
    parents: tuple[str, ...]


def largest(current: float | None, value: float) -> float:
    return value if current is None else max(current, value)


class FeatureQueries:
    def __init__(self, wikidata: Wikidata):
        self.wikidata = wikidata

    def ranked(self, classes: tuple[str, ...], min_sitelinks: int) -> list[str]:
        links: dict[str, int] = {}
        for batch in itertools.batched(sorted(self.with_subclasses(classes)), BATCH):
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
        for batch in itertools.batched(sorted(qids), BATCH):
            rows = self.wikidata._select(f"""
                SELECT ?item ?itemLabel ?links ?image ?coordinates ?article ?height ?elevation ?length ?area ?heritage WHERE {{
                  VALUES ?item {{ {values_clause(batch)} }}
                  ?item wikibase:sitelinks ?links .
                  OPTIONAL {{ ?item wdt:P18 ?image }}
                  OPTIONAL {{ ?item wdt:P625 ?coordinates }}
                  OPTIONAL {{ ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> }}
                  OPTIONAL {{ ?item p:P2048/psn:P2048/wikibase:quantityAmount ?height }}
                  OPTIONAL {{ ?item p:P2044/psn:P2044/wikibase:quantityAmount ?elevation }}
                  OPTIONAL {{ ?item p:P2043/psn:P2043/wikibase:quantityAmount ?length }}
                  OPTIONAL {{ ?item p:P2046/psn:P2046/wikibase:quantityAmount ?area }}
                  OPTIONAL {{ ?item wdt:P1435 ?heritage . FILTER(?heritage = wd:{WORLD_HERITAGE_SITE}) }}
                  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul" }}
                }}""")
            for row in rows:
                self._merge(found, row)
        return found

    def _merge(self, found: dict[str, ItemFacts], row: dict[str, str]) -> None:
        qid = qid_of(row["item"])
        facts = found.setdefault(qid, ItemFacts(display_name(row["itemLabel"]), int(row["links"])))
        facts.image = facts.image or row.get("image")
        facts.wikipedia = facts.wikipedia or row.get("article")
        if facts.coordinates is None and (point := POINT.match(row.get("coordinates", ""))):
            facts.coordinates = (float(point.group(1)), float(point.group(2)))
        if "height" in row:
            facts.height_m = largest(facts.height_m, float(row["height"]))
        if "elevation" in row:
            facts.elevation_m = largest(facts.elevation_m, float(row["elevation"]))
        if "length" in row:
            facts.length_km = largest(facts.length_km, float(row["length"]) / METRES_PER_KILOMETRE)
        if "area" in row:
            facts.area_km2 = largest(facts.area_km2, float(row["area"]) / SQUARE_METRES_PER_SQUARE_KILOMETRE)
        facts.world_heritage = facts.world_heritage or "heritage" in row

    def instances_of(self, qids: set[str], cls: str) -> set[str]:
        return self._matching(qids, f"?item wdt:P31 ?type . ?type wdt:P279* wd:{cls} .")

    def inhabited(self, qids: set[str]) -> set[str]:
        return self._matching(qids, "?item wdt:P1082 ?population . FILTER(?population > 0) FILTER NOT EXISTS { ?item wdt:P576 ?dissolved }")

    def _matching(self, qids: set[str], condition: str) -> set[str]:
        found = set()
        for batch in itertools.batched(sorted(qids), BATCH):
            rows = self.wikidata._select(f"""
                SELECT DISTINCT ?item WHERE {{
                  VALUES ?item {{ {values_clause(batch)} }}
                  {condition}
                }}""")
            found.update(qid_of(row["item"]) for row in rows)
        return found

    def cities(self, qids: set[str]) -> dict[str, list[str]]:
        places: dict[str, dict[str, str]] = defaultdict(dict)
        for batch in itertools.batched(sorted(qids), BATCH):
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
