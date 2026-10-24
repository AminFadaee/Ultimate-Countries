import json
import logging
import pathlib

from geography.countrydeck.cities import collect_cities
from geography.countrydeck.maps import (
    city_jobs,
    draw_city_maps,
    jobs_for,
    render_area_maps,
)
from geography.countrydeck.names import official_language
from geography.countrydeck.neighbours import neighbours_of
from geography.countrydeck.places import CountryPlacesCollector
from geography.countrydeck.provinces import collect_provinces
from geography.data import NaturalEarth
from geography.sources.http import create_session
from geography.sources.wikidata import Wikidata

logger = logging.getLogger(__name__)
COUNTRY_DECKS_DIR = pathlib.Path("data/country_decks")


def load_documents(countries_dir: pathlib.Path) -> dict[str, dict]:
    documents = (json.loads(path.read_text()) for path in countries_dir.glob("*.json"))
    return {document["codes"]["alpha_3"]: document for document in documents if document["codes"]["alpha_3"]}


def write(path: pathlib.Path, content) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")


def collect_country(code: str, language: str | None = None, include_overseas: bool = False,
                    countries_dir: pathlib.Path = pathlib.Path("data/countries"), output: pathlib.Path = COUNTRY_DECKS_DIR) -> pathlib.Path:
    documents = load_documents(countries_dir)
    country = documents[code]
    session = create_session()
    wikidata, data = Wikidata(session), NaturalEarth.load()
    language = language or official_language(wikidata, country["id"])
    target = output / code
    meta = {"code": code, "id": country["id"], "name": country["name"], "alpha_2": country["codes"]["alpha_2"], "language": language}
    write(target / "meta.json", meta)
    provinces = collect_provinces(wikidata, data, code, language, include_overseas)
    write(target / "provinces.json", provinces)
    write(target / "cities.json", collect_cities(wikidata, data, code, country["id"], language, provinces))
    write(target / "neighbours.json", neighbours_of(session, data, code, documents))
    logger.info("Collected %s into %s", country["name"], target)
    return target


def run_country(code: str, language: str | None = None, include_overseas: bool = False, rerender_maps: bool = False, workers: int = 2,
                countries_dir: pathlib.Path = pathlib.Path("data/countries")) -> None:
    target = collect_country(code, language, include_overseas)
    jobs = [job for job in jobs_for(target) if rerender_maps or not (job.output.exists() and job.answer_output.exists())]
    for job, error in render_area_maps(jobs, workers):
        if error:
            logger.error("Map failed for %s: %s", job.label, error)
    documents = load_documents(countries_dir)
    draw_city_maps(city_jobs(target, documents), workers, rerender_maps)
    run_places(target, code, documents[code], rerender_maps, workers)


def run_places(directory: pathlib.Path, code: str, country: dict, rerender_maps: bool, workers: int) -> None:
    meta = json.loads((directory / "meta.json").read_text())
    excluded = {city["id"] for city in json.loads((directory / "cities.json").read_text())}
    excluded |= {province["id"] for province in json.loads((directory / "provinces.json").read_text())}
    collector = CountryPlacesCollector(directory, create_session())
    features = collector.collect(NaturalEarth.load(), code, country, meta["language"], excluded)
    slugs = collector.finish(features)
    collector.draw_maps(features, slugs, code, rerender_maps, workers)
    logger.info("Exported %d places to %s", collector.export(features, slugs), directory / "places")
