import itertools
import json
import re
import time
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass

import requests

from geography.models import City
from geography.sources.http import TIMEOUT

SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
TITLE_BATCH = 50
DETAIL_BATCH = 300
QUERY_ATTEMPTS = 3
RETRY_DELAY = 10

SETTLEMENT_KINDS = ("Q486972", "Q15284")
CITY_POPULATION_FLOORS = (100_000, 10_000, 0)
CITY_CANDIDATES = 30
LAKE = "Q23397"
LABEL_LANGUAGES = ("en", "mul")

POINT = re.compile(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)")
DISAMBIGUATION = re.compile(r"\s*\([^)]*\)$")

YEAR_LENGTH = 4
DATE_LENGTH_BY_PRECISION = {10: 7, 11: 10}


@dataclass(frozen=True)
class Inception:
    date: str
    preferred: bool


@dataclass(frozen=True)
class OfficialLanguage:
    name: str
    codes: frozenset[str]
    regional: bool


@dataclass(frozen=True)
class NamedPlace:
    name: str
    qid: str
    latitude: float
    longitude: float


def display_name(label: str) -> str:
    return DISAMBIGUATION.sub("", label).strip()


def qid_of(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def formatted_time(time: str, precision: int) -> str:
    return time[: DATE_LENGTH_BY_PRECISION.get(precision, YEAR_LENGTH)]


def values_clause(qids) -> str:
    return " ".join(f"wd:{qid}" for qid in qids)


class Wikidata:
    def __init__(self, session: requests.Session):
        self.session = session

    def resolve_codes(self, alpha_2_codes: dict[str, str]) -> dict[str, str]:
        key_by_code = {code: key for key, code in alpha_2_codes.items()}
        codes = " ".join(f'"{code}"' for code in key_by_code)
        rows = self._select(f"""
            SELECT ?code ?item WHERE {{
              VALUES ?code {{ {codes} }}
              ?item p:P297 ?statement .
              ?statement ps:P297 ?code ; wikibase:rank ?rank .
              FILTER(?rank != wikibase:DeprecatedRank)
              FILTER NOT EXISTS {{ ?item wdt:P576 ?dissolved }}
            }}""")
        items = defaultdict(set)
        for row in rows:
            items[row["code"]].add(qid_of(row["item"]))
        return {key_by_code[code]: next(iter(found)) for code, found in items.items() if len(found) == 1}

    def resolve(self, wikipedia_urls: dict[str, str]) -> dict[str, str]:
        titles = {
            key: urllib.parse.unquote(url.rsplit("/wiki/", 1)[-1]).replace("_", " ")
            for key, url in wikipedia_urls.items()
        }
        qid_by_title = {}
        for batch in itertools.batched(sorted(set(titles.values())), TITLE_BATCH):
            qid_by_title.update(self._resolve_titles(batch))
        return {key: qid_by_title[title] for key, title in titles.items() if title in qid_by_title}

    def parts_of(self, pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
        if not pairs:
            return set()
        values = " ".join(f"(wd:{part} wd:{whole})" for part, whole in pairs)
        rows = self._select(f"""
            SELECT ?part ?whole WHERE {{
              VALUES (?part ?whole) {{ {values} }}
              ?part wdt:P131|wdt:P361 ?whole .
            }}""")
        return {(qid_of(row["part"]), qid_of(row["whole"])) for row in rows}

    def official_languages(self, qids: set[str]) -> dict[str, list[OfficialLanguage]]:
        rows = self._select(f"""
            SELECT ?c ?language ?languageLabel ?iso1 ?iso3 ?part WHERE {{
              VALUES ?c {{ {values_clause(qids)} }}
              ?c p:P37 ?statement .
              ?statement ps:P37 ?language ; wikibase:rank ?rank .
              FILTER(?rank != wikibase:DeprecatedRank)
              FILTER NOT EXISTS {{ ?statement pq:P582 ?ended }}
              OPTIONAL {{ ?statement pq:P518 ?part . FILTER EXISTS {{ ?part wdt:P17 ?partCountry }} }}
              OPTIONAL {{ ?language wdt:P218 ?iso1 }}
              OPTIONAL {{ ?language wdt:P220 ?iso3 }}
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul" }}
            }}""")
        found = defaultdict(dict)
        for row in rows:
            language = qid_of(row["language"])
            known = found[qid_of(row["c"])].get(language)
            codes = {code for code in (row.get("iso1"), row.get("iso3")) if code}
            regional = "part" in row and (known is None or known.regional)
            if known:
                codes |= known.codes
            found[qid_of(row["c"])][language] = OfficialLanguage(row["languageLabel"], frozenset(codes), regional)
        return {
            qid: sorted(languages.values(), key=lambda language: (language.regional, language.name))
            for qid, languages in found.items()
        }

    def memberships_joined_since(self, qids: set[str], organizations: dict[str, str], since: str) -> dict[str, set[str]]:
        label_by_qid = {qid: label for label, qid in organizations.items()}
        rows = self._select(f"""
            SELECT ?c ?organization (MAX(?start) AS ?joined) WHERE {{
              VALUES ?c {{ {values_clause(qids)} }}
              VALUES ?organization {{ {values_clause(label_by_qid)} }}
              ?c p:P463 ?membership .
              ?membership ps:P463 ?organization ; wikibase:rank ?membershipRank .
              ?organization p:P527 ?part .
              ?part ps:P527 ?c ; wikibase:rank ?partRank .
              FILTER(?membershipRank != wikibase:DeprecatedRank && ?partRank != wikibase:DeprecatedRank)
              FILTER NOT EXISTS {{ ?membership pq:P582 ?left }}
              FILTER NOT EXISTS {{ ?part pq:P582 ?removed }}
              {{ ?membership pq:P580 ?start }} UNION {{ ?part pq:P580 ?start }}
            }}
            GROUP BY ?c ?organization""")
        memberships = defaultdict(set)
        for row in rows:
            if row["joined"][:10] >= since:
                memberships[qid_of(row["c"])].add(label_by_qid[qid_of(row["organization"])])
        return dict(memberships)

    def by_geonames_ids(self, geonames_ids: set[str]) -> dict[str, str]:
        found = {}
        for batch in itertools.batched(sorted(geonames_ids), DETAIL_BATCH):
            identifiers = " ".join(f'"{identifier}"' for identifier in batch)
            rows = self._select(f"""
                SELECT ?id ?city WHERE {{
                  VALUES ?id {{ {identifiers} }}
                  ?city wdt:P1566 ?id .
                }}""")
            for row in rows:
                found.setdefault(row["id"], qid_of(row["city"]))
        return found

    def inceptions(self, qids: set[str]) -> dict[str, list[Inception]]:
        rows = self._select(f"""
            SELECT ?c ?time ?precision ?rank WHERE {{
              VALUES ?c {{ {values_clause(qids)} }}
              ?c p:P571 ?statement .
              ?statement psv:P571 [ wikibase:timeValue ?time ; wikibase:timePrecision ?precision ] ;
                         wikibase:rank ?rank .
              FILTER(?rank != wikibase:DeprecatedRank && !STRSTARTS(STR(?time), "-"))
            }}""")
        inceptions = defaultdict(list)
        for row in rows:
            date = formatted_time(row["time"], int(row["precision"]))
            inceptions[qid_of(row["c"])].append(Inception(date, row["rank"].endswith("PreferredRank")))
        return dict(inceptions)

    def forms_of_government(self, qids: set[str]) -> dict[str, list[str]]:
        rows = self._select(f"""
            SELECT ?c ?formLabel WHERE {{
              VALUES ?c {{ {values_clause(qids)} }}
              ?c p:P122 ?statement .
              ?statement ps:P122 ?form ; wikibase:rank ?rank .
              FILTER(?rank != wikibase:DeprecatedRank)
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul" }}
            }}""")
        forms = defaultdict(list)
        for row in rows:
            forms[qid_of(row["c"])].append(row["formLabel"])
        return {qid: sorted(labels) for qid, labels in forms.items()}

    def official_religions(self, qids: set[str]) -> dict[str, list[str]]:
        rows = self._select(f"""
            SELECT ?c ?religionLabel WHERE {{
              VALUES ?c {{ {values_clause(qids)} }}
              ?c wdt:P3075 ?religion .
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul" }}
            }}""")
        religions = defaultdict(set)
        for row in rows:
            religions[qid_of(row["c"])].add(row["religionLabel"])
        return {qid: sorted(found) for qid, found in religions.items()}

    def demonyms(self, qids: set[str]) -> dict[str, list[str]]:
        rows = self._select(f"""
            SELECT ?c ?demonym WHERE {{
              VALUES ?c {{ {values_clause(qids)} }}
              ?c wdt:P1549 ?demonym .
              FILTER(lang(?demonym) = "en")
            }}""")
        demonyms = defaultdict(list)
        for row in rows:
            demonyms[qid_of(row["c"])].append(row["demonym"])
        return dict(demonyms)

    def capitals(self, qids: set[str]) -> dict[str, list[str]]:
        rows = self._select(f"""
            SELECT ?c ?capital WHERE {{
              VALUES ?c {{ {values_clause(qids)} }}
              ?c wdt:P36 ?capital .
            }}""")
        capitals = defaultdict(set)
        for row in rows:
            capitals[qid_of(row["c"])].add(qid_of(row["capital"]))
        return {qid: sorted(found) for qid, found in capitals.items()}

    def cities_named(self, country_qid: str, names: list[str]) -> dict[str, str]:
        labels = " ".join(f"{json.dumps(name, ensure_ascii=False)}@{language}" for name in names for language in LABEL_LANGUAGES)
        rows = self._select(f"""
            SELECT ?label ?city ?links WHERE {{
              VALUES ?label {{ {labels} }}
              ?city rdfs:label|skos:altLabel ?label ; wdt:P17 wd:{country_qid} ; wikibase:sitelinks ?links .
              VALUES ?kind {{ {values_clause(SETTLEMENT_KINDS)} }}
              FILTER EXISTS {{ ?city wdt:P31/wdt:P279* ?kind }}
            }} ORDER BY DESC(?links)""")
        found = {}
        for row in rows:
            found.setdefault(row["label"], qid_of(row["city"]))
        return found

    def settlements_named(self, names: list[str]) -> list[NamedPlace]:
        labels = " ".join(f"{json.dumps(name, ensure_ascii=False)}@{language}" for name in names for language in LABEL_LANGUAGES)
        rows = self._select(f"""
            SELECT DISTINCT ?label ?city ?coordinates WHERE {{
              VALUES ?label {{ {labels} }}
              ?city rdfs:label|skos:altLabel ?label ; wdt:P625 ?coordinates .
              VALUES ?kind {{ {values_clause(SETTLEMENT_KINDS)} }}
              FILTER EXISTS {{ ?city wdt:P31/wdt:P279* ?kind }}
            }}""")
        places = []
        for row in rows:
            point = POINT.match(row["coordinates"])
            if point:
                places.append(NamedPlace(row["label"], qid_of(row["city"]), float(point.group(2)), float(point.group(1))))
        return places

    def flag_urls(self, qids: set[str]) -> dict[str, str]:
        rows = self._select(f"""
            SELECT ?c ?flag WHERE {{
              VALUES ?c {{ {values_clause(qids)} }}
              ?c wdt:P41 ?flag .
            }}""")
        return {qid_of(row["c"]): row["flag"] for row in rows}

    def osm_relations(self, qids: set[str]) -> dict[str, str]:
        rows = self._select(f"""
            SELECT ?c ?osm WHERE {{
              VALUES ?c {{ {values_clause(qids)} }}
              ?c wdt:P402 ?osm .
            }}""")
        return {qid_of(row["c"]): row["osm"] for row in rows}

    def large_lakes(self, min_area_m2: float) -> list[str]:
        rows = self._select(f"""
            SELECT DISTINCT ?osm WHERE {{
              ?lake wdt:P402 ?osm ; p:P2046/psn:P2046/wikibase:quantityAmount ?area .
              FILTER(?area > {min_area_m2})
              ?lake wdt:P31/wdt:P279* wd:{LAKE} .
            }}""")
        return sorted({row["osm"] for row in rows})

    def ranked_cities(self, qid: str, excluded: set[str], count: int) -> list[str]:
        for floor in CITY_POPULATION_FLOORS:
            cities = [city for city in self._ranked_cities(qid, floor) if city not in excluded]
            if len(cities) >= count or floor == CITY_POPULATION_FLOORS[-1]:
                return cities[:count]
        return []

    def city_details(self, city_qids: set[str]) -> dict[str, City]:
        details = {}
        for batch in itertools.batched(sorted(city_qids), DETAIL_BATCH):
            rows = self._select(f"""
                SELECT ?city ?cityLabel ?links ?coordinates ?osm WHERE {{
                  VALUES ?city {{ {values_clause(batch)} }}
                  ?city wikibase:sitelinks ?links .
                  OPTIONAL {{ ?city wdt:P625 ?coordinates }}
                  OPTIONAL {{ ?city wdt:P402 ?osm }}
                  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul" }}
                }}""")
            populations = self._populations(batch)
            for row in rows:
                qid = qid_of(row["city"])
                if qid in details:
                    continue
                point = POINT.match(row.get("coordinates", ""))
                details[qid] = City(
                    id=qid,
                    name=display_name(row["cityLabel"]),
                    longitude=float(point.group(1)) if point else None,
                    latitude=float(point.group(2)) if point else None,
                    population=populations.get(qid),
                    sitelinks=int(row["links"]),
                    osm_relation=row.get("osm"),
                )
        return details

    def _populations(self, qids) -> dict[str, int]:
        rows = self._select(f"""
            SELECT ?city ?population ?rank ?date WHERE {{
              VALUES ?city {{ {values_clause(qids)} }}
              ?city p:P1082 ?statement .
              ?statement ps:P1082 ?population ; wikibase:rank ?rank .
              FILTER(?rank != wikibase:DeprecatedRank)
              OPTIONAL {{ ?statement pq:P585 ?date }}
            }}""")
        best = {}
        for row in rows:
            key = (row["rank"].endswith("PreferredRank"), row.get("date", ""), float(row["population"]))
            qid = qid_of(row["city"])
            if qid not in best or key > best[qid]:
                best[qid] = key
        return {qid: int(key[2]) for qid, key in best.items()}

    def _resolve_titles(self, titles) -> dict[str, str]:
        response = self.session.get(
            WIKIPEDIA_API_URL,
            params={
                "action": "query",
                "prop": "pageprops",
                "ppprop": "wikibase_item",
                "redirects": 1,
                "titles": "|".join(titles),
                "format": "json",
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        query = response.json()["query"]
        forward = {item["from"]: item["to"] for key in ("normalized", "redirects") for item in query.get(key, [])}
        qid_by_page = {
            page["title"]: page["pageprops"]["wikibase_item"]
            for page in query["pages"].values()
            if "wikibase_item" in page.get("pageprops", {})
        }
        resolved = {}
        for title in titles:
            target = title
            while target in forward:
                target = forward[target]
            if target in qid_by_page:
                resolved[title] = qid_by_page[target]
        return resolved

    def _ranked_cities(self, qid: str, floor: int) -> list[str]:
        rows = self._select(f"""
            SELECT ?city ?links WHERE {{
              {{ SELECT DISTINCT ?city ?links WHERE {{
                  ?city wdt:P17 wd:{qid} ; wdt:P1082 ?population ; wikibase:sitelinks ?links .
                  FILTER(?population > {floor})
                }} ORDER BY DESC(?links) LIMIT {CITY_CANDIDATES} }}
              VALUES ?kind {{ {values_clause(SETTLEMENT_KINDS)} }}
              FILTER EXISTS {{ ?city wdt:P31/wdt:P279* ?kind }}
            }} ORDER BY DESC(?links)""")
        return list(dict.fromkeys(qid_of(row["city"]) for row in rows))

    def _select(self, query: str) -> list[dict[str, str]]:
        for attempt in range(1, QUERY_ATTEMPTS + 1):
            response = self.session.post(
                SPARQL_URL,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            try:
                bindings = response.json()["results"]["bindings"]
            except requests.JSONDecodeError:
                if attempt == QUERY_ATTEMPTS:
                    raise
                time.sleep(RETRY_DELAY * attempt)
                continue
            return [{name: binding["value"] for name, binding in row.items()} for row in bindings]
        return []
