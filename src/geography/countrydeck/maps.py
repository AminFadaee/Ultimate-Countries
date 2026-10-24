import json
import logging
import multiprocessing
import pathlib
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass

from geography.data import NaturalEarth
from geography.detail import DetailLayers
from geography.map_cache import through_cache
from geography.maps import our_country_names
from geography.naming import slugify
from geography.render import Framing, LocatorMap, Naming, Scene

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AreaMapJob:
    kind: str
    host: str
    target: str
    label: str
    output: pathlib.Path
    answer_output: pathlib.Path
    marker: tuple[float, float] | None = None


def empty(frame):
    return frame.iloc[0:0]


class AreaMapWorker:
    renderer: LocatorMap | None = None

    @classmethod
    def start(cls) -> None:
        cls.renderer = LocatorMap(NaturalEarth.load(), DetailLayers.load(), country_names=our_country_names())

    @classmethod
    def render(cls, job: AreaMapJob) -> str:
        data = cls.renderer.data
        country = data.country(job.host)
        if job.kind == "province":
            highlight = country.regions[country.regions["wikidataid"] == job.target].geometry
            regions = country.regions.geometry
        else:
            highlight = data.country(job.target).core.geometry
            regions = empty(country.core.geometry)
        scene = Scene(
            highlight=highlight, frame=country.core.geometry, disputed=empty(country.core.geometry), host=country.core.geometry,
            regions=regions, framing=Framing.ANCHORED, naming=Naming(job.label), marker=job.marker,
        )
        job.output.parent.mkdir(parents=True, exist_ok=True)
        cls.renderer.render(scene, job.output, job.answer_output)
        return job.target


def jobs_for(directory: pathlib.Path) -> list[AreaMapJob]:
    meta = json.loads((directory / "meta.json").read_text())
    host = meta["code"]
    maps = directory / "maps"
    jobs = []
    for province in json.loads((directory / "provinces.json").read_text()):
        centre = province["centre"]
        marker = (centre["longitude"], centre["latitude"]) if centre and centre["longitude"] is not None else None
        output = maps / f"province__{slugify(province['name'])}.png"
        jobs.append(AreaMapJob("province", host, province["id"], province["name"], output, answer_path(output), marker))
    for neighbour in json.loads((directory / "neighbours.json").read_text()):
        output = maps / f"neighbour__{slugify(neighbour['name'])}.png"
        jobs.append(AreaMapJob("neighbour", host, neighbour["alpha_3"], neighbour["name"], output, answer_path(output)))
    logger.info("%d maps for %s", len(jobs), meta["name"])
    return jobs


def answer_path(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(f"{path.stem}-answer{path.suffix}")


def render_area_maps(jobs: list[AreaMapJob], workers: int) -> Iterable[tuple[AreaMapJob, Exception | None]]:
    return through_cache(jobs, lambda missing: draw_area_maps(missing, workers))


def draw_area_maps(jobs: list[AreaMapJob], workers: int) -> Iterable[tuple[AreaMapJob, Exception | None]]:
    if not jobs:
        return
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context, initializer=AreaMapWorker.start) as pool:
        futures = {pool.submit(AreaMapWorker.render, job): job for job in jobs}
        for done, future in enumerate(as_completed(futures), start=1):
            if done % 10 == 0 or done == len(jobs):
                logger.info("Country maps: %d/%d rendered", done, len(jobs))
            yield futures[future], future.exception()
