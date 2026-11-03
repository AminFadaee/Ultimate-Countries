import collections
import dataclasses
import json
import re

import pytest

from geography.countrydeck.names import normalised
from geography.features.lengths import disagrees
from geography.island_frames import ISLAND_FRAMES
from geography.naming import answer_path
from tests.conftest import (
    COUNTRY_DECKS,
    DATA,
    ROOT,
    compare_snapshot,
    country_documents,
    feature_documents,
)

FEATURES = feature_documents()
COUNTRIES = country_documents()
ACRONYM = re.compile(r"^[A-Z]{2,}$")
CONTESTED_ALIAS = "Arabian Gulf"


def deck_name(root) -> str:
    return "Ultimate Places" if root == DATA else root.name


def test_every_place_has_its_map():
    missing = [path.name for root, path, document in FEATURES if not document["map"] or not (root / document["map"]).exists()]
    assert not missing


def test_every_country_deck_place_has_an_answer_map():
    missing = [path.name for root, path, document in FEATURES if root != DATA and not answer_path(root / document["map"]).exists()]
    assert not missing


def test_every_photo_exists():
    missing = [path.name for root, path, document in FEATURES if document["photo"] and not (root / document["photo"]["file"]).exists()]
    assert not missing


def test_every_country_has_its_flag_and_map():
    missing = [document["name"] for document in COUNTRIES if not (DATA / document["flag"]).exists() or not (DATA / document["map"]).exists()]
    assert not missing


def test_place_keys_and_maps_are_unique_within_a_deck():
    for root in {root for root, _, _ in FEATURES}:
        documents = [document for owner, _, document in FEATURES if owner == root]
        keys = collections.Counter(document["key"] for document in documents)
        maps = collections.Counter(document["map"] for document in documents)
        assert not [key for key, count in keys.items() if count > 1], deck_name(root)
        assert not [name for name, count in maps.items() if count > 1], deck_name(root)


def test_country_names_are_written_out():
    assert not [document["name"] for document in COUNTRIES if ACRONYM.match(document["name"]) or document["name"] == "Congo"]


def test_no_text_carries_the_contested_alias():
    found = [path.name for _, path, document in FEATURES if CONTESTED_ALIAS in json.dumps(document, ensure_ascii=False)]
    found += [document["name"] for document in COUNTRIES if CONTESTED_ALIAS in json.dumps(document, ensure_ascii=False)]
    assert not found


def test_native_names_are_normalised_and_keep_half_spaces():
    natives = [document["native"] for _, _, document in FEATURES if document.get("native")]
    for directory in COUNTRY_DECKS.iterdir():
        for name in ("provinces.json", "cities.json"):
            natives += [entry["native"] for entry in json.loads((directory / name).read_text()) if entry.get("native")]
    assert not [native for native in natives if normalised(native) != native]
    assert not [native for native in natives if "بینالمللی" in native]


def test_no_length_contradicts_its_description():
    found = [path.name for _, path, document in FEATURES if document["length_km"] and document["description"] and disagrees(document["length_km"], document["description"])]
    assert not found


def test_every_drawn_river_records_its_mouth():
    rivers = [(path.name, document["river"]) for _, path, document in FEATURES if document["kind"] == "River" and document.get("river")]
    assert rivers
    assert not [name for name, facts in rivers if not facts.get("mouth")]


def test_the_readme_place_count_matches_the_data():
    stated = int(re.search(r"(\d+) places, each drawn on a map", (ROOT / "README.md").read_text()).group(1))
    assert stated == sum(1 for root, _, _ in FEATURES if root == DATA)


def test_island_frames_name_real_countries():
    codes = {document["codes"]["alpha_3"] for document in COUNTRIES}
    assert not set(ISLAND_FRAMES) - codes


def test_agreed_island_frames_are_kept(updating):
    compare_snapshot("island_frames.json", {code: dataclasses.asdict(frame) for code, frame in ISLAND_FRAMES.items()}, updating)


@pytest.mark.parametrize("name", ["provinces.json", "cities.json", "neighbours.json"])
def test_country_deck_entries_are_named(name):
    for directory in COUNTRY_DECKS.iterdir():
        assert not [entry for entry in json.loads((directory / name).read_text()) if not entry.get("name")], directory.name
