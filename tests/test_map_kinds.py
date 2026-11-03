import json
from dataclasses import replace

import pytest

from geography.countrydeck.collect import load_documents
from geography.countrydeck.maps import AreaMapWorker, city_jobs, jobs_for
from geography.features.catalog import natural_earth_shapes
from geography.features.kinds import Kind
from geography.features.maps import FeatureMapJob, FeatureMapWorker
from geography.labels import CityLabel
from geography.maps import MapJob, MapWorker
from geography.naming import answer_path
from tests.conftest import COUNTRY_DECKS, DATA

pytestmark = pytest.mark.slow
COUNTRIES = ["FRA", "RUS", "TUV", "NLD", "CHL", "FJI", "KIR", "LUX", "USA", "IRN"]
CITIES = [("CZE", "Prague"), ("BEL", "Brussels"), ("DEU", "Cologne"), ("NLD", "Amsterdam")]
DECK_MAPS = {"IRN": ["Hamadan", "Golestan", "Kurdistan", "Turkey"], "NLD": ["Friesland", "Flevoland", "South Holland", "Belgium"]}
DECK_CITIES = {"IRN": ["Arak", "Kermanshah", "Andimeshk"], "NLD": ["Breda", "Schiedam", "Eindhoven"]}
PLACES = ["adriatic_sea", "africa", "air_mountains", "anatolia", "atacama_desert", "brazilian_highlands", "central_america",
          "danube_delta", "grand_canyon", "great_barrier_reef", "great_bear_lake", "isthmus_of_panama", "panama_canal", "pantanal"]


@pytest.fixture(scope="module")
def documents():
    return load_documents(DATA / "countries")


@pytest.fixture(scope="module")
def renderers():
    MapWorker.start()
    AreaMapWorker.start()
    FeatureMapWorker.start()


@pytest.mark.parametrize("code", COUNTRIES)
def test_country_maps_pass_their_checks(renderers, documents, code, tmp_path):
    document = documents[code]
    MapWorker.render(MapJob(document["id"], document["id"], tmp_path / "map.png", document["name"], code))
    assert MapWorker.renderer.problems == []


@pytest.mark.parametrize(("code", "name"), CITIES)
def test_city_maps_pass_their_checks(renderers, documents, code, name, tmp_path):
    document = documents[code]
    cities = document["famous_cities"]
    target = next(city for city in cities if city["name"] == name)
    labels = tuple(CityLabel(city["name"], city["longitude"], city["latitude"], city["name"] in document["capitals"], city["id"] == target["id"]) for city in cities)
    output = tmp_path / "city.png"
    MapWorker.render(MapJob(target["id"], document["id"], output, document["name"], code, name, None, (target["longitude"], target["latitude"]), answer_path(output), labels))
    assert MapWorker.renderer.problems == []


@pytest.mark.parametrize(("deck", "label"), [(deck, label) for deck, labels in DECK_MAPS.items() for label in labels])
def test_province_and_neighbour_maps_pass_their_checks(renderers, deck, label, tmp_path):
    job = next(job for job in jobs_for(COUNTRY_DECKS / deck) if job.label == label)
    AreaMapWorker.render(replace(job, output=tmp_path / "map.png", answer_output=tmp_path / "map-answer.png"))
    assert AreaMapWorker.renderer.problems == []


@pytest.mark.parametrize(("deck", "name"), [(deck, name) for deck, names in DECK_CITIES.items() for name in names])
def test_country_deck_city_maps_pass_their_checks(renderers, documents, deck, name, tmp_path):
    job = next(job for job in city_jobs(COUNTRY_DECKS / deck, documents) if job.place_name == name)
    MapWorker.render(replace(job, output=tmp_path / "city.png", answer_output=tmp_path / "city-answer.png"))
    assert MapWorker.renderer.problems == []


@pytest.mark.parametrize("slug", PLACES)
def test_place_maps_pass_their_checks(renderers, slug, tmp_path):
    document = json.loads((DATA / "places" / "features" / f"{slug}.json").read_text())
    shape = natural_earth_shapes()[document["qid"]]
    FeatureMapWorker.render(FeatureMapJob(document["key"], Kind(document["kind"]), shape, tmp_path / "place.png", None, False))
    assert FeatureMapWorker.renderer.problems == []
