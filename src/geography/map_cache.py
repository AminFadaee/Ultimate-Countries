import dataclasses
import hashlib
import itertools
import logging
import os
import pathlib
import shutil
import time
from collections.abc import Callable, Iterable
from importlib import metadata

import geopandas as gpd

from geography.data import DATA_DIR
from geography.detail import CACHE_DIR

MAP_CACHE_DIR = CACHE_DIR / "maps"
OUTPUT_FIELDS = ("output", "answer_output")
MAPS_PER_BATCH = 20
UNUSED_DAYS = 90
CHUNK = 1 << 20
RENDERER_FILES = (
    "render.py",
    "images.py",
    "maps.py",
    "labels.py",
    "detail.py",
    "data.py",
    "places.py",
    "features/maps.py",
    "features/kinds.py",
    "sources/natural_earth_physical.py",
    "country_labels.py",
    "countrydeck/maps.py",
)
LIBRARIES = ("matplotlib", "cartopy", "shapely", "geopandas", "pillow", "pyproj", "numpy", "pandas")
BASE_DATA = ((DATA_DIR, "*.zip"), (CACHE_DIR, "detail_*.gpkg"), (CACHE_DIR, "ne_10m_*.zip"))

logger = logging.getLogger(__name__)


def file_digest(path: pathlib.Path) -> str:
    content = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            content.update(chunk)
    return content.hexdigest()


def setting_digest() -> str:
    content = hashlib.sha256()
    for name in RENDERER_FILES:
        content.update((pathlib.Path(__file__).parent / name).read_bytes())
    for library in LIBRARIES:
        content.update(f"{library}={metadata.version(library)}".encode())
    for directory, pattern in BASE_DATA:
        for path in sorted(directory.glob(pattern)):
            content.update(path.name.encode())
            content.update(file_digest(path).encode())
    return content.hexdigest()


def feed(content, value) -> None:
    if isinstance(value, gpd.GeoSeries):
        content.update(b"".join(geometry.wkb for geometry in value if geometry is not None))
    elif isinstance(value, pathlib.Path) and value.exists():
        content.update(file_digest(value).encode())
    else:
        content.update(repr(value).encode())


def outputs_of(job) -> list[pathlib.Path]:
    return [path for name in OUTPUT_FIELDS if (path := getattr(job, name, None)) is not None]


class MapCache:
    def __init__(self, directory: pathlib.Path = MAP_CACHE_DIR):
        self.directory = directory
        self.setting = setting_digest()
        self._prune()

    def _prune(self) -> None:
        cutoff = time.time() - UNUSED_DAYS * 24 * 3600
        for path in self.directory.glob("*.png"):
            if path.stat().st_mtime < cutoff:
                path.unlink()

    def fingerprint(self, job) -> str:
        content = hashlib.sha256(self.setting.encode())
        for field in dataclasses.fields(job):
            if field.name not in OUTPUT_FIELDS:
                content.update(field.name.encode())
                feed(content, getattr(job, field.name))
        return content.hexdigest()

    def entries(self, job) -> list[tuple[pathlib.Path, pathlib.Path]]:
        key = self.fingerprint(job)
        return [(self.directory / f"{key}-{index}.png", path) for index, path in enumerate(outputs_of(job))]

    def reuse(self, job) -> bool:
        entries = self.entries(job)
        if not entries or not all(cached.exists() for cached, _ in entries):
            return False
        for cached, output in entries:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cached, output)
            os.utime(cached)
        return True

    def keep(self, job) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        for cached, output in self.entries(job):
            if output.exists():
                shutil.copyfile(output, cached)


def through_cache(jobs: list, render: Callable[[list], Iterable[tuple]]) -> Iterable[tuple]:
    cache = MapCache()
    missing = []
    for job in jobs:
        if cache.reuse(job):
            yield job, None
        else:
            missing.append(job)
    logger.info("Maps: %d reused, %d to draw", len(jobs) - len(missing), len(missing))
    for batch in itertools.batched(missing, MAPS_PER_BATCH, strict=False):
        for job, error in render(list(batch)):
            if error is None:
                cache.keep(job)
            yield job, error
