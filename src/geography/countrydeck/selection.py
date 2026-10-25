import itertools
import re
from dataclasses import dataclass

from geography.features.kinds import Kind
from geography.features.wikidata import FeatureQueries
from geography.sources.wikidata import DETAIL_BATCH, Wikidata, qid_of, values_clause

MIN_LINKS = 12
UNESCO_MIN_LINKS = 5
HILL = "Q54050"
POLDER = "Q106259"
FLOOR_SHARE = 0.3
HERITAGE_MIN_LINKS = 20
HERITAGE_CAP = 60
WORLD_HERITAGE_SITE = "Q9259"
HUMAN_SETTLEMENT = "Q486972"
VOLCANO_CLASSES = ("Q8072", "Q159954")
NOT_BUILT_HERITAGE = re.compile(r"ramsar|tentative|world heritage", re.IGNORECASE)
COMPONENT_ID = re.compile(r"-\d+$")
CRITERION = re.compile(r"\(([ivx]+)\)")
NATURAL_CRITERIA = frozenset({"vii", "viii", "ix", "x"})
PHOTO_CARD_KINDS = frozenset({Kind.LANDMARK, Kind.VOLCANO, Kind.WATERFALL, Kind.CANYON, Kind.CANAL})

NATURAL = (
    (Kind.MOUNTAIN, ("Q8502",), 10),
    (Kind.MOUNTAIN_RANGE, ("Q46831",), 6),
    (Kind.LAKE, ("Q23397", "Q131681"), 8),
    (Kind.RIVER, ("Q4022",), 12),
    (Kind.ISLAND, ("Q23442",), 10),
    (Kind.DESERT, ("Q8514",), 5),
    (Kind.PLATEAU, ("Q75520",), 3),
    (Kind.PLAIN, ("Q160091",), 3),
    (Kind.VALLEY, ("Q39816",), 3),
    (Kind.SEA, ("Q39594", "Q37901"), 8),
    (Kind.PENINSULA, ("Q34763",), 4),
    (Kind.WATERFALL, ("Q34038",), 5),
    (Kind.CANYON, ("Q150784",), 5),
    (Kind.PARK, ("Q46169",), 6),
    (Kind.CANAL, ("Q12284",), 4),
    (Kind.POLDER, ("Q106259",), 5),
)
FUNCTIONAL = (
    ("Q33506", 4, "Museum"), ("Q1248784", 3, "Airport"), ("Q483110", 3, "Stadium"), ("Q15310171", 2, "Port"), ("Q2416723", 2, "Theme_park"),
    ("Q570116", 6, ""), ("Q2630707", 5, "Water_defence"), ("Q1187968", 2, "Water_defence"), ("Q12323", 2, "Dam"), ("Q2994840", 2, "Garden"),
    ("Q756102", 2, "Museum"),
)
SEAPORT = "Q15310171"
ADMINISTRATIVE_KINDS = (Kind.ISLAND, Kind.PENINSULA, Kind.PARK)


@dataclass
class Candidate:
    qid: str
    kind: Kind
    links: int
    tier: str
    group: str = ""


def ranked(wikidata: Wikidata, country_qid: str, classes: tuple[str, ...], floor: int = MIN_LINKS) -> list[tuple[str, int]]:
    found: dict[str, int] = {}
    for cls in classes:
        rows = wikidata._select(f"""
            SELECT DISTINCT ?i ?links WHERE {{
              ?i wdt:P17 wd:{country_qid} ; wdt:P31/wdt:P279* wd:{cls} ; wikibase:sitelinks ?links . FILTER(?links >= {floor})
            }} ORDER BY DESC(?links) LIMIT 300""")
        found.update({qid_of(row["i"]): int(row["links"]) for row in rows})
    return sorted(found.items(), key=lambda item: -item[1])


def above_floor(items: list[tuple[str, int]], minimum: int = MIN_LINKS) -> list[tuple[str, int]]:
    third = items[min(2, len(items) - 1)][1] if items else 0
    floor = max(minimum, FLOOR_SHARE * third)
    return [item for item in items if item[1] >= floor]


def unesco(wikidata: Wikidata, country_qid: str) -> list[tuple[str, int]]:
    rows = wikidata._select(f"""
        SELECT ?i ?id ?links WHERE {{ ?i wdt:P17 wd:{country_qid} ; wdt:P757 ?id ; wikibase:sitelinks ?links }}""")
    best: dict[str, tuple[str, int]] = {}
    for row in rows:
        if COMPONENT_ID.search(row["id"]):
            continue
        key = re.sub(r"\D*$", "", row["id"]) or row["id"]
        item = (qid_of(row["i"]), int(row["links"]))
        if item[1] < UNESCO_MIN_LINKS:
            continue
        if key not in best or item[1] > best[key][1]:
            best[key] = item
    return sorted(best.values(), key=lambda item: -item[1])


def heritage(wikidata: Wikidata, country_qid: str) -> list[tuple[str, int]]:
    rows = wikidata._select(f"""
        SELECT ?i ?links ?label WHERE {{
          ?i wdt:P17 wd:{country_qid} ; wdt:P1435 ?designation ; wikibase:sitelinks ?links . FILTER(?links >= {HERITAGE_MIN_LINKS - 6})
          ?designation rdfs:label ?label . FILTER(lang(?label) = "en")
        }}""")
    items: dict[str, int] = {}
    for row in rows:
        if not NOT_BUILT_HERITAGE.search(row["label"]):
            items[qid_of(row["i"])] = int(row["links"])
    return sorted(items.items(), key=lambda item: -item[1])


def extremes(wikidata: Wikidata, country_qid: str) -> dict[str, list[str]]:
    highest = wikidata._select(f"SELECT ?p WHERE {{ wd:{country_qid} wdt:P610 ?p }}")
    by_height = wikidata._select(f"""
        SELECT ?i (MAX(?height) AS ?top) WHERE {{
          ?i wdt:P17 wd:{country_qid} ; wdt:P2044 ?height ; wdt:P31/wdt:P279* ?class . VALUES ?class {{ wd:Q8502 wd:{HILL} }}
        }} GROUP BY ?i ORDER BY DESC(?top) LIMIT 25""")
    lowest = wikidata._select(f"SELECT ?p WHERE {{ wd:{country_qid} wdt:P1589 ?p }}")
    return {
        "highest": list(dict.fromkeys([qid_of(row["p"]) for row in highest] + [qid_of(row["i"]) for row in by_height])),
        "lowest": [qid_of(row["p"]) for row in lowest],
    }


def inscribed(wikidata: Wikidata, qids: set[str]) -> set[str]:
    found: set[str] = set()
    for batch in itertools.batched(sorted(qids), DETAIL_BATCH, strict=False):
        rows = wikidata._select(f"""
            SELECT DISTINCT ?i WHERE {{ VALUES ?i {{ {values_clause(batch)} }} ?i wdt:P757 ?id }}""")
        found |= {qid_of(row["i"]) for row in rows}
        rows = wikidata._select(f"""
            SELECT DISTINCT ?i WHERE {{ VALUES ?i {{ {values_clause(batch)} }} ?i wdt:P361/wdt:P361? ?whole . ?whole wdt:P757 ?id }}""")
        found |= {qid_of(row["i"]) for row in rows}
    return found


def wholes(wikidata: Wikidata, qids: set[str]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for batch in itertools.batched(sorted(qids), DETAIL_BATCH, strict=False):
        rows = wikidata._select(f"SELECT DISTINCT ?i ?whole WHERE {{ VALUES ?i {{ {values_clause(batch)} }} ?i wdt:P361/wdt:P361? ?whole }}")
        for row in rows:
            found.setdefault(qid_of(row["i"]), set()).add(qid_of(row["whole"]))
    return found


def natural_sites(wikidata: Wikidata, qids: set[str]) -> set[str]:
    criteria: dict[str, set[str]] = {}
    for batch in itertools.batched(sorted(qids), DETAIL_BATCH, strict=False):
        rows = wikidata._select(f"""
            SELECT ?i ?label WHERE {{ VALUES ?i {{ {values_clause(batch)} }} ?i wdt:P2614 ?criterion . ?criterion rdfs:label ?label . FILTER(lang(?label) = "en") }}""")
        for row in rows:
            if match := CRITERION.search(row["label"]):
                criteria.setdefault(qid_of(row["i"]), set()).add(match.group(1))
    return {qid for qid, found in criteria.items() if found <= NATURAL_CRITERIA}


def select_places(wikidata: Wikidata, country_qid: str, excluded: set[str]) -> tuple[list[Candidate], dict[str, list[str]]]:
    queries = FeatureQueries(wikidata)
    chosen: dict[str, Candidate] = {}

    def add(items: list[tuple[str, int]], kind: Kind, tier: str, cap: int | None = None, group: str = "") -> None:
        for qid, links in items[:cap]:
            if qid not in excluded and qid not in chosen:
                chosen[qid] = Candidate(qid, kind, links, tier, group)

    seaports = {qid for qid, _ in ranked(wikidata, country_qid, (SEAPORT,), 8)}
    naturals = []
    for kind, classes, cap in NATURAL:
        items = above_floor(ranked(wikidata, country_qid, classes))
        items = [item for item in items if item[0] not in seaports]
        if kind is Kind.MOUNTAIN:
            volcanoes = set().union(*(queries.instances_of({qid for qid, _ in items}, cls) for cls in VOLCANO_CLASSES))
            naturals.append((Kind.VOLCANO, [item for item in items if item[0] in volcanoes], cap))
            items = [item for item in items if item[0] not in volcanoes]
        naturals.append((kind, items, cap))
    natural_kind = {qid: kind for kind, items, _ in reversed(naturals) for qid, _ in items}
    for qid, links in unesco(wikidata, country_qid):
        add([(qid, links)], natural_kind.get(qid, Kind.LANDMARK), "unesco")
    for kind, items, cap in naturals:
        add(items, kind, "natural", cap)
    built = heritage(wikidata, country_qid)
    third = built[min(2, len(built) - 1)][1] if built else 0
    built = [item for item in built if item[1] >= max(HERITAGE_MIN_LINKS, FLOOR_SHARE * third)]
    settlements = queries.instances_of({qid for qid, _ in built}, HUMAN_SETTLEMENT)
    add([item for item in built if item[0] not in settlements], Kind.LANDMARK, "heritage", HERITAGE_CAP)
    for cls, cap, group in FUNCTIONAL:
        add(ranked(wikidata, country_qid, (cls,))[:cap], Kind.LANDMARK, "functional", group=group)
    found = extremes(wikidata, country_qid)
    polders = queries.instances_of(set(found["lowest"]), POLDER)
    for qid in found["highest"]:
        chosen.setdefault(qid, Candidate(qid, Kind.MOUNTAIN, 0, "highest"))
    for qid in found["lowest"]:
        chosen.setdefault(qid, Candidate(qid, Kind.POLDER if qid in polders else Kind.PLAIN, 0, "lowest"))
    checked = {
        qid
        for qid, candidate in chosen.items()
        if (candidate.tier == "natural" or candidate.tier == "functional" and not candidate.group) and candidate.kind not in ADMINISTRATIVE_KINDS
    }
    settlements = queries.instances_of(checked, HUMAN_SETTLEMENT)
    return [candidate for candidate in chosen.values() if candidate.qid not in settlements and candidate.qid not in excluded], found
