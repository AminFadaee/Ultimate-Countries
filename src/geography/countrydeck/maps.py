import json
import logging
import multiprocessing
import pathlib
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace

import geopandas as gpd
from shapely.errors import GEOSException
from shapely.geometry import shape

from geography.data import NaturalEarth
from geography.detail import DetailLayers
from geography.features.kinds import Kind, Shape
from geography.features.maps import (
    LINE_BAND_SHARE,
    PLACE_ZOOM_THRESHOLD,
    POINT_LENS_SPAN_M,
    POINT_RADIUS_M,
    local_projection,
    outline,
    size_of,
)
from geography.features.spatial import CRS
from geography.labels import CityLabel
from geography.map_cache import through_cache
from geography.maps import MapJob, our_country_names, render_maps
from geography.naming import answer_path, slugify
from geography.render import (
    NEARBY_PART_DISTANCE,
    Framing,
    LocatorMap,
    Naming,
    Scene,
    get_mainland,
    get_nearby_parts,
)

logger = logging.getLogger(__name__)
TOUCHING_M = 1_000
ACROSS_WATER_REACH_M = 700_000


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
            highlight=highlight, frame=country.core.geometry if job.kind == "province" else frame_with_neighbour(country.core.geometry, highlight), disputed=empty(country.core.geometry), host=country.core.geometry,
            regions=regions, framing=Framing.ANCHORED, naming=Naming(job.label), marker=job.marker,
        )
        job.output.parent.mkdir(parents=True, exist_ok=True)
        cls.renderer.render(scene, job.output, job.answer_output)
        return job.target


def frame_with_neighbour(country: gpd.GeoSeries, neighbour: gpd.GeoSeries) -> gpd.GeoSeries:
    mainland = get_mainland(country)
    local = local_projection(mainland)
    home = mainland.to_crs(local).union_all()
    shape = neighbour.to_crs(local).union_all()
    reach = home.buffer(NEARBY_PART_DISTANCE if shape.distance(home) < TOUCHING_M else ACROSS_WATER_REACH_M)
    nearest = shape.intersection(reach)
    if nearest.is_empty:
        return country
    return gpd.GeoSeries([*country.geometry, *gpd.GeoSeries([nearest], crs=local).to_crs(CRS).geometry], crs=CRS)


@dataclass(frozen=True)
class PlaceMapJob:
    key: str
    kind: str
    shape: dict
    host: str
    label: str
    photo: pathlib.Path | None
    output: pathlib.Path
    answer_output: pathlib.Path


def place_scene(job: PlaceMapJob, data: NaturalEarth) -> Scene:
    kind = Kind(job.kind)
    country = data.country(job.host)
    geometry = gpd.GeoSeries([shape(job.shape)], crs=CRS)
    common = {
        "frame": country.core.geometry, "disputed": country.disputed.geometry, "regions": country.regions.geometry, "framing": Framing.ANCHORED,
        "country": country.core.geometry, "photo": job.photo, "naming": Naming(job.label), "zoom_threshold": PLACE_ZOOM_THRESHOLD,
    }
    local = local_projection(geometry)
    if geometry.geom_type.iloc[0] == "Point":
        spot = geometry.to_crs(local).buffer(POINT_RADIUS_M).to_crs(CRS)
        return Scene(highlight=spot, host=country.core.geometry, lens_highlight=spot, min_zoom_span=POINT_LENS_SPAN_M, style=kind.spec.palette.style(Shape.POINT), **common)
    if kind.spec.shape.is_line:
        mainland = get_nearby_parts(country.core.geometry, get_mainland(country.core.geometry))
        band = geometry.to_crs(local).buffer(size_of(mainland) * LINE_BAND_SHARE).to_crs(CRS)
        return Scene(highlight=band, host=empty(country.core.geometry), lens_highlight=band, style=kind.spec.palette.style(Shape.LINE), **common)
    marked = outline(geometry, kind)
    return Scene(highlight=marked, host=geometry, lens_highlight=marked, style=kind.spec.palette.style(Shape.AREA), **common)


class PlaceMapWorker:
    renderer: LocatorMap | None = None

    @classmethod
    def start(cls) -> None:
        cls.renderer = LocatorMap(NaturalEarth.load(), DetailLayers.load(), country_names=our_country_names())

    @classmethod
    def render(cls, job: PlaceMapJob) -> str:
        job.output.parent.mkdir(parents=True, exist_ok=True)
        try:
            scene = place_scene(job, cls.renderer.data)
            cls.renderer.render(scene, job.output, job.answer_output)
        except (ValueError, TypeError, GEOSException):
            point = shape(job.shape).representative_point()
            fallback = replace(job, shape={"type": "Point", "coordinates": (point.x, point.y)})
            cls.renderer.render(place_scene(fallback, cls.renderer.data), job.output, job.answer_output)
        return job.key


def render_place_maps(jobs: list[PlaceMapJob], workers: int) -> Iterable[tuple[PlaceMapJob, Exception | None]]:
    return through_cache(jobs, lambda missing: draw_place_maps(missing, workers))


def draw_place_maps(jobs: list[PlaceMapJob], workers: int) -> Iterable[tuple[PlaceMapJob, Exception | None]]:
    if not jobs:
        return
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context, initializer=PlaceMapWorker.start) as pool:
        futures = {pool.submit(PlaceMapWorker.render, job): job for job in jobs}
        for done, future in enumerate(as_completed(futures), start=1):
            if done % 10 == 0 or done == len(jobs):
                logger.info("Place maps: %d/%d rendered", done, len(jobs))
            yield futures[future], future.exception()


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


def city_jobs(directory: pathlib.Path, documents: dict[str, dict]) -> list[MapJob]:
    meta = json.loads((directory / "meta.json").read_text())
    country = documents[meta["code"]]
    capitals = {city["id"] for city in country["cities"] if city["role"] == "capital"}
    cities = sorted(json.loads((directory / "cities.json").read_text()), key=lambda city: -city["population"])
    maps = directory / "maps"
    jobs = []
    for city in cities:
        labels = tuple(CityLabel(other["name"], other["longitude"], other["latitude"], other["id"] in capitals, other["id"] == city["id"]) for other in cities)
        output = maps / f"city__{slugify(city['name'])}.png"
        jobs.append(MapJob(city["id"], country["id"], output, country["name"], meta["code"], city["name"], None, (city["longitude"], city["latitude"]),
                           answer_path(output), labels))
    return jobs


def draw_city_maps(jobs: list[MapJob], workers: int, rerender: bool) -> None:
    pending = [job for job in jobs if rerender or not (job.output.exists() and job.answer_output.exists())]
    for job, error in render_maps(pending, workers):
        if error:
            logger.error("Map failed for %s: %s", job.place_name, error)


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
