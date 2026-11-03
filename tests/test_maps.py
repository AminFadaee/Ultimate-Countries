import functools
import pathlib

import numpy as np
import pytest
from PIL import Image, ImageColor

from geography.features.kinds import Kind
from geography.features.maps import RIVER_BLUE
from geography.render import Color
from tests.conftest import COUNTRY_DECKS, DATA

MAP_SIZE = (2000, 1052)
MAX_FEATURE_SHARE = 0.45
OUTLINED = ("maps/countries", "maps/cities", "maps/province__", "maps/city__", "maps/neighbour__")


def map_files() -> list[pathlib.Path]:
    folders = [DATA / "maps" / "countries", DATA / "maps" / "cities", DATA / "places" / "maps"]
    folders += [path for deck in COUNTRY_DECKS.iterdir() for path in (deck / "maps", deck / "places" / "maps")]
    return sorted(path for folder in folders for path in folder.glob("*.png"))


@functools.cache
def feature_colours() -> np.ndarray:
    colours = {Color.HIGHLIGHT, Color.HOST, Color.DISPUTED, RIVER_BLUE}
    for kind in Kind:
        colours |= {kind.spec.palette.outline, kind.spec.palette.fill}
    return np.array([r * 65536 + g * 256 + b for r, g, b in (ImageColor.getrgb(colour)[:3] for colour in colours)])


def feature_share(image: Image.Image) -> float:
    if image.mode != "P":
        pixels = np.asarray(image.convert("RGB")).reshape(-1, 3).astype(np.int64)
        return float(np.isin(pixels[:, 0] * 65536 + pixels[:, 1] * 256 + pixels[:, 2], feature_colours()).mean())
    palette = np.array(image.getpalette(), dtype=np.int64).reshape(-1, 3)
    wanted = np.flatnonzero(np.isin(palette[:, 0] * 65536 + palette[:, 1] * 256 + palette[:, 2], feature_colours()))
    counts = np.bincount(np.asarray(image).ravel(), minlength=len(palette))
    return float(counts[wanted].sum() / counts.sum())


MAPS = map_files()


def test_there_are_maps_to_check():
    assert len(MAPS) > 1500


def test_every_map_has_the_card_size():
    odd = []
    for path in MAPS:
        with Image.open(path) as image:
            if image.size != MAP_SIZE:
                odd.append(f"{path.relative_to(DATA)} is {image.size}")
    assert not odd


@pytest.mark.parametrize("path", MAPS, ids=lambda path: str(path.relative_to(DATA)))
def test_no_feature_fills_the_map(path):
    with Image.open(path) as image:
        share = feature_share(image)
    assert share <= MAX_FEATURE_SHARE
    if any(marker in str(path) for marker in OUTLINED) and not path.stem.endswith("-answer"):
        assert share > 0, "the highlighted area is not visible"
