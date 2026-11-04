import dataclasses
import json
import pathlib
import re
import sqlite3
import tempfile
import zipfile

import pytest

from tests.conftest import BUILD, COUNTRY_DECKS, DATA, SNAPSHOTS, country_documents

pytestmark = pytest.mark.decks
MEDIA_REFERENCE = re.compile(r'src="([^"]+)"|\[sound:([^\]]+)\]')
BARE_SOUND = re.compile(r'(?<![="\w:-])[\w.-]+\.(?:mp3|ogg|wav|opus)\b(?!")')
FIELD_SEPARATOR = "\x1f"


@dataclasses.dataclass(frozen=True)
class Note:
    guid: str
    model: str
    fields: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Package:
    name: str
    models: dict[str, dict]
    notes: tuple[Note, ...]
    media: frozenset[str]


def read_package(path: pathlib.Path) -> Package:
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as folder:
        archive.extract("collection.anki2", folder)
        media = frozenset(json.loads(archive.read("media")).values())
        with sqlite3.connect(pathlib.Path(folder) / "collection.anki2") as collection:
            models = json.loads(collection.execute("SELECT models FROM col").fetchone()[0])
            rows = collection.execute("SELECT guid, mid, flds FROM notes").fetchall()
    notes = tuple(Note(guid, str(mid), tuple(fields.split(FIELD_SEPARATOR))) for guid, mid, fields in rows)
    return Package(path.stem, models, notes, media)


@pytest.fixture(scope="module")
def packages() -> dict[str, Package]:
    paths = sorted(BUILD.glob("*.apkg"))
    if not paths:
        pytest.skip("build the decks first")
    return {path.stem: read_package(path) for path in paths}


def test_note_ids_are_unique_across_all_decks(packages):
    seen: dict[str, str] = {}
    clashes = []
    for package in packages.values():
        for note in package.notes:
            if note.guid in seen:
                clashes.append(f"{note.fields[0]} in {seen[note.guid]} and {package.name}")
            seen[note.guid] = package.name
    assert not clashes


def test_every_media_reference_is_in_the_package(packages):
    missing = [
        f"{package.name}: {name}"
        for package in packages.values() for note in package.notes for field in note.fields
        for match in MEDIA_REFERENCE.finditer(field) if (name := match.group(1) or match.group(2)) not in package.media
    ]
    assert not missing


def test_sounds_are_referenced_so_anki_exports_them(packages):
    bare = [f"{package.name}: {note.fields[0]}" for package in packages.values() for note in package.notes for field in note.fields if BARE_SOUND.search(field)]
    assert not bare


def test_note_counts_match_the_data(packages):
    expected = {"ultimate_places": len(list((DATA / "places" / "features").glob("*.json")))}
    countries = country_documents()
    expected["ultimate_countries"] = len(countries) + sum(1 for path in (DATA / "maps" / "cities").glob("*.png") if not path.stem.endswith("-answer"))
    for deck in COUNTRY_DECKS.iterdir():
        meta = json.loads((deck / "meta.json").read_text())
        listed = sum(len(json.loads((deck / name).read_text())) for name in ("provinces.json", "cities.json", "neighbours.json"))
        expected[f"ultimate_{meta['name'].lower()}_geography"] = listed + len(list((deck / "places" / "features").glob("*.json")))
    assert {name: len(package.notes) for name, package in packages.items()} == expected


def deck_shape(package: Package) -> dict:
    models = {model_id: {"name": model["name"], "fields": [field["name"] for field in model["flds"]], "templates": [template["name"] for template in model["tmpls"]]}
              for model_id, model in package.models.items()}
    notes = {note.guid: f"{note.model} {note.fields[0]}" for note in package.notes}
    return {"models": models, "notes": notes}


def test_decks_keep_their_note_types_and_notes(packages, updating):
    changes = []
    for name, package in packages.items():
        path = SNAPSHOTS / f"deck_{name}.json"
        current = deck_shape(package)
        if updating or not path.exists():
            path.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
            continue
        known = json.loads(path.read_text())
        if current["models"] != known["models"]:
            changes.append(f"{name}: note types changed, which breaks importing over an older version")
        removed = sorted(known["notes"][guid] for guid in known["notes"].keys() - current["notes"].keys())
        added = sorted(current["notes"][guid] for guid in current["notes"].keys() - known["notes"].keys())
        moved = sorted(f"{known['notes'][guid]} -> {current['notes'][guid]}" for guid in known["notes"].keys() & current["notes"].keys() if known["notes"][guid] != current["notes"][guid])
        changes += [f"{name}: {kind} {items}" for kind, items in (("removed", removed), ("added", added), ("changed", moved)) if items]
    assert not changes, "\n".join(changes) + "\nif these are intended, run pytest --update-snapshots"


def test_every_map_can_be_tapped_to_zoom(packages):
    missing = []
    for name, package in packages.items():
        for model in package.models.values():
            for template in model["tmpls"]:
                for side in ("qfmt", "afmt"):
                    if 'class="value image map' in template[side] and "zoom-overlay" not in template[side]:
                        missing.append(f"{name}: {model['name']} {template['name']} {side}")
    assert not missing, "\n".join(missing)


def test_every_answer_has_its_links(packages):
    missing = [f"{name}: {model['name']} {template['name']}" for name, package in packages.items() for model in package.models.values()
               for template in model["tmpls"] if "links" not in template["afmt"] or "GoToLinkedPage" not in template["afmt"]]
    assert not missing, "\n".join(missing)
