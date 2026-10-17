import logging
import math
from dataclasses import dataclass

import geopandas as gpd

from geography.sources.wikidata import Wikidata
from geography.sources.wikipedia import Wikipedia

MIN_FAME = 180
MIN_POPULATION = 100_000
MIN_VISITED_FAME = 100
CITIES_PER_COUNTRY = 8
VISITED_BONUS = 0.5

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    qid: str
    country_id: str
    fame: int
    population: int
    visited: bool


class FamousCities:
    def __init__(self, wikidata: Wikidata, wikipedia: Wikipedia, places: gpd.GeoDataFrame, countries: dict[str, str]):
        self.wikidata = wikidata
        self.wikipedia = wikipedia
        self.places = places
        self.countries = countries

    def select(self) -> dict[str, list[str]]:
        visited = self._visited()
        candidates = self._candidates(visited)
        fame = self.wikidata.sitelink_counts({qid for qid, _ in candidates})
        sizeable = {qid for qid, _ in candidates if fame.get(qid, 0) >= MIN_VISITED_FAME}
        population = self.wikidata.populations(sizeable)
        country_ids = set(self.countries.values())
        pool = [
            Candidate(qid, country_id, fame.get(qid, 0), population.get(qid, 0), qid in visited)
            for qid, country_id in candidates
            if qid not in country_ids
        ]
        eligible = [candidate for candidate in pool if self._eligible(candidate)]
        return self._capped(eligible)

    def _visited(self) -> set[str]:
        urls = self.wikipedia.most_visited_cities()
        return set(self.wikidata.resolve(dict(enumerate(urls))).values())

    def _candidates(self, visited: set[str]) -> list[tuple[str, str]]:
        found = {}
        for place in self.places.itertuples():
            country_id = self.countries.get(place.adm0_a3) or self.countries.get(place.iso_a2)
            if country_id and isinstance(place.wikidataid, str) and (place.pop_max >= MIN_POPULATION or place.wikidataid in visited):
                found.setdefault(place.wikidataid, country_id)
        return list(found.items())

    @staticmethod
    def _eligible(candidate: Candidate) -> bool:
        famous = candidate.fame >= MIN_FAME and candidate.population >= MIN_POPULATION
        return famous or (candidate.visited and candidate.fame >= MIN_VISITED_FAME)

    @staticmethod
    def _capped(eligible: list[Candidate]) -> dict[str, list[str]]:
        top_fame = max(candidate.fame for candidate in eligible)
        top_population = max(candidate.population for candidate in eligible)

        def score(candidate: Candidate) -> float:
            size = math.log10(candidate.population) / math.log10(top_population) if candidate.population > 0 else 0
            return candidate.fame / top_fame + size + (VISITED_BONUS if candidate.visited else 0)

        selected: dict[str, list[str]] = {}
        for candidate in sorted(eligible, key=score, reverse=True):
            chosen = selected.setdefault(candidate.country_id, [])
            if len(chosen) < CITIES_PER_COUNTRY:
                chosen.append(candidate.qid)
        return selected
