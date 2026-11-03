import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BUILD = ROOT / "build"
SNAPSHOTS = pathlib.Path(__file__).parent / "snapshots"
COUNTRY_DECKS = DATA / "country_decks"


def pytest_addoption(parser):
    parser.addoption("--record", action="store_true", help="fetch and store HTTP responses the recorded tests are missing")
    parser.addoption("--update-snapshots", action="store_true", help="rewrite the snapshots in tests/snapshots from the current state")


@pytest.fixture(scope="session")
def recording(request) -> bool:
    return request.config.getoption("--record")


@pytest.fixture(scope="session")
def updating(request) -> bool:
    return request.config.getoption("--update-snapshots")


def feature_roots() -> list[pathlib.Path]:
    return [DATA, *sorted(path for path in COUNTRY_DECKS.iterdir() if path.is_dir())]


def feature_documents() -> list[tuple[pathlib.Path, pathlib.Path, dict]]:
    return [(root, path, json.loads(path.read_text())) for root in feature_roots() for path in sorted((root / "places" / "features").glob("*.json"))]


def country_documents() -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted((DATA / "countries").glob("*.json"))]


def compare_snapshot(name: str, current, updating: bool) -> None:
    path = SNAPSHOTS / name
    if updating or not path.exists():
        path.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        return
    assert json.loads(json.dumps(current, ensure_ascii=False)) == json.loads(path.read_text()), f"{name} changed; if this is intended, run pytest --update-snapshots"


@pytest.fixture(scope="session")
def researched(recording):
    from tests.support.rivers import research

    found = {}

    def get(expected):
        if expected.id not in found:
            found[expected.id] = research(expected.deck, expected.slug, recording)
        return found[expected.id]
    return get
