import itertools
import re
from dataclasses import dataclass

from geography.sources.http import TIMEOUT
from geography.sources.wikidata import (
    DETAIL_BATCH,
    Wikidata,
    qid_of,
    shorter_name,
    values_clause,
)

TYPE_SUFFIX = re.compile(r"\s+(province|state|county|region)$", re.IGNORECASE)
POINT = re.compile(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)")
WIKIPEDIA_SITE = "https://en.wikipedia.org/"
UNIT_TYPES = ("Q34876", "Q35657", "Q28575", "Q82794")
ENTITY_API = "https://www.wikidata.org/w/api.php"
ENTITY_BATCH = 50


@dataclass(frozen=True)
class Names:
    name: str
    native: str | None


def official_language(wikidata: Wikidata, country_qid: str) -> str:
    rows = wikidata._select(f"""
        SELECT ?code WHERE {{ wd:{country_qid} p:P37 ?statement . ?statement ps:P37 ?language ; wikibase:rank ?rank .
          ?language wdt:P424 ?code . FILTER(?rank != wikibase:DeprecatedRank) }} ORDER BY DESC(?rank)""")
    if not rows:
        raise LookupError(f"No official language on Wikidata for {country_qid}; pass a language code")
    return rows[0]["code"]


def unit_words(wikidata: Wikidata, language: str) -> set[str]:
    rows = wikidata._select(f"""
        SELECT ?label WHERE {{ VALUES ?type {{ {values_clause(UNIT_TYPES)} }} ?type rdfs:label ?label . FILTER(lang(?label) = "{language}") }}""")
    return {row["label"] for row in rows}


def without_unit_word(native: str | None, words: set[str]) -> str | None:
    if native is None:
        return None
    for word in words:
        for stripped in (native.removeprefix(f"{word} "), native.removesuffix(f" {word}")):
            if stripped != native:
                return stripped
    return native


def native_labels(wikidata: Wikidata, qids: list[str], language: str) -> dict[str, str]:
    found = {}
    for batch in itertools.batched(qids, ENTITY_BATCH, strict=False):
        params = {"action": "wbgetentities", "ids": "|".join(batch), "props": "labels", "languages": language, "format": "json"}
        response = wikidata.session.get(ENTITY_API, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        for qid, entity in response.json()["entities"].items():
            if label := entity.get("labels", {}).get(language):
                found[qid] = label["value"]
    return found


def named(wikidata: Wikidata, qids: set[str], language: str, strip_type: bool = False) -> dict[str, Names]:
    found: dict[str, Names] = {}
    words = unit_words(wikidata, language) if strip_type else set()
    for batch in itertools.batched(sorted(qids), DETAIL_BATCH, strict=False):
        rows = wikidata._select(f"""
            SELECT ?i ?label ?title ?native WHERE {{
              VALUES ?i {{ {values_clause(batch)} }}
              OPTIONAL {{ ?i rdfs:label ?label . FILTER(lang(?label) = "en") }}
              OPTIONAL {{ ?i rdfs:label ?mul . FILTER(lang(?mul) = "mul") }}
              OPTIONAL {{ ?article schema:about ?i ; schema:isPartOf <{WIKIPEDIA_SITE}> ; schema:name ?title }}
              OPTIONAL {{ ?i rdfs:label ?native . FILTER(lang(?native) = "{language}") }}
            }}""")
        exact = native_labels(wikidata, list(batch), language)
        for row in rows:
            qid = qid_of(row["i"])
            label = row.get("label") or row.get("mul") or row.get("native") or qid
            name = shorter_name(label, row.get("title"))
            found.setdefault(qid, Names(TYPE_SUFFIX.sub("", name) if strip_type else name, without_unit_word(exact.get(qid, row.get("native")), words)))
    return found
