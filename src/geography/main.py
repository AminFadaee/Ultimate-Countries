import argparse
import json
import logging
import os
import pathlib

from dotenv import load_dotenv

from geography import cards, quality
from geography.collect import Collector, DataPaths, Options
from geography.data import NaturalEarth
from geography.db import Database
from geography.deck import build_deck
from geography.features.collect import PlacesCollector, PlacesOptions, PlacesPaths
from geography.features.deck import build_places_deck
from geography.naming import slugify
from geography.places import PlaceFinder
from geography.render import Borders, LocatorMap
from geography.sources.http import create_session

MAPS_DIR = pathlib.Path("maps")
DATA_DIR = pathlib.Path("data")
ERROR_LOG = pathlib.Path("errors.log")
REFERENCE_FILE = pathlib.Path("reference/countries.json")
BUILD_DIR = pathlib.Path("build")
DECK_FILE = BUILD_DIR / "ultimate_countries.apkg"
PLACES_DECK_FILE = BUILD_DIR / "ultimate_places.apkg"

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    console = logging.StreamHandler()
    errors = logging.FileHandler(ERROR_LOG, mode="w")
    errors.setLevel(logging.ERROR)
    logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[console, errors])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect country data and generate locator maps.")
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect", help="refresh the country database, flags and maps")
    collect.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    collect.add_argument("--refresh-cities", action="store_true", help="re-rank cities even if recently updated")
    collect.add_argument("--rerender-maps", action="store_true", help="render maps even if they already exist")
    collect.add_argument("--skip-maps", action="store_true", help="collect data without rendering maps")
    collect.add_argument("--workers", type=int, default=Options.workers, help="parallel map renderers")

    check = commands.add_parser("check", help="score the dataset against the reference set and scan for anomalies")
    check.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    check.add_argument("--reference", type=pathlib.Path, default=REFERENCE_FILE)
    check.add_argument("--details", action="store_true", help="list every failure and anomaly")

    deck = commands.add_parser("deck", help="build the Anki deck from the exported country data")
    deck.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    deck.add_argument("--output", type=pathlib.Path, default=DECK_FILE)

    places = commands.add_parser("places", help="collect geographic features and landmarks for Ultimate Places")
    places.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    places.add_argument("--rerender-maps", action="store_true", help="render maps even if they already exist")
    places.add_argument("--workers", type=int, default=PlacesOptions.workers, help="parallel map renderers")

    places_deck = commands.add_parser("places-deck", help="build the Ultimate Places Anki deck")
    places_deck.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    places_deck.add_argument("--output", type=pathlib.Path, default=PLACES_DECK_FILE)

    place = commands.add_parser("place", help="render a city, island or other area")
    place.add_argument("name")
    place.add_argument("--country", required=True)
    place.add_argument("--output", type=pathlib.Path, default=MAPS_DIR)
    place.add_argument("--borders", type=Borders, choices=list(Borders), default=Borders.COUNTRIES)

    return parser.parse_args()


def render_place(output_dir: pathlib.Path, borders: Borders, name: str, country: str) -> None:
    renderer = LocatorMap(NaturalEarth.load())
    place = PlaceFinder(renderer.data).find(name, country)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{slugify(f'{name} {country} {borders}')}.png"
    renderer.render_place(place, output, borders)
    logger.info("Saved %s", output)


def check(data_dir: pathlib.Path, reference_file: pathlib.Path, details: bool) -> None:
    documents = quality.load_documents(DataPaths(data_dir).countries)
    reference = json.loads(reference_file.read_text())
    print(quality.format_report(quality.score(documents, reference), quality.anomalies(documents), details))
    print()
    print(cards.format_coverage(documents))


def collect(data_dir: pathlib.Path, options: Options) -> None:
    load_dotenv()
    paths = DataPaths(data_dir)
    database = Database(paths.database)
    try:
        collector = Collector(database, paths, create_session(), os.environ["RESTCOUNTRIES_API_KEY"])
        collector.run(options)
    finally:
        database.close()


def main() -> None:
    args = parse_args()
    configure_logging()

    match args.command:
        case "collect":
            options = Options(args.refresh_cities, args.rerender_maps, args.skip_maps, args.workers)
            collect(args.data_dir, options)
        case "check":
            check(args.data_dir, args.reference, args.details)
        case "deck":
            documents = quality.load_documents(DataPaths(args.data_dir).countries)
            build_deck(documents, args.data_dir, BUILD_DIR, args.output)
        case "places":
            PlacesCollector(PlacesPaths(args.data_dir), create_session()).run(PlacesOptions(args.rerender_maps, args.workers))
        case "places-deck":
            documents = [json.loads(path.read_text()) for path in sorted(PlacesPaths(args.data_dir).features.glob("*.json"))]
            build_places_deck(documents, args.data_dir, BUILD_DIR, args.output)
        case "place":
            render_place(args.output, args.borders, args.name, args.country)


if __name__ == "__main__":
    main()
