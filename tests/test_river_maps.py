import json
from dataclasses import replace

import pytest

from geography.countrydeck.collect import load_documents
from geography.countrydeck.maps import (
    PlaceMapJob,
    PlaceMapWorker,
    city_labels,
    deck_cities,
    river_place_scene,
)
from geography.features.kinds import Kind
from geography.features.maps import (
    FeatureMapJob,
    FeatureMapWorker,
    View,
    river_scene,
    view_for,
)
from geography.features.riverart.checks import RiverCheck
from geography.naming import answer_path
from tests.conftest import DATA
from tests.support.rivers import DECKS
from tests.test_river_research import RIVERS

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def workers():
    FeatureMapWorker.start()
    PlaceMapWorker.start()
    return load_documents(DATA / "countries")


@pytest.mark.parametrize("expected", RIVERS, ids=lambda expected: expected.id)
def test_river_maps_pass_their_checks(researched, workers, expected, tmp_path):
    river = researched(expected)
    output = tmp_path / f"{expected.id}.png"
    check = RiverCheck(river.art)
    if expected.deck == "UP":
        job = FeatureMapJob(river.feature.key, Kind.RIVER, river.art.river, output, None, False, art=river.art)
        if view_for(job, FeatureMapWorker.renderer.aspect_ratio) is not View.REGIONAL:
            pytest.skip("drawn in a world or centred view, without river art")
        FeatureMapWorker.renderer.render(replace(river_scene(job), overlay=check.paint, inspect=check.inspect), output)
    else:
        root, _ = DECKS[expected.deck]
        cities = city_labels(deck_cities(root), workers[json.loads((root / "meta.json").read_text())["code"]])
        job = PlaceMapJob(river.feature.key, Kind.RIVER.value, {}, expected.deck, river.document["name"], None, output, answer_path(output), river.art, cities)
        PlaceMapWorker.renderer.render(river_place_scene(job, check), output, answer_path(output))
    assert output.exists()
    assert not check.problems
