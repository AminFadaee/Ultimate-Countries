import logging
import multiprocessing
import pathlib
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, shape

from geography.data import Country, NaturalEarth
from geography.detail import AREA_TYPES, DetailLayers, simplify
from geography.places import Place
from geography.render import Borders, Framing, LocatorMap, Scene

POINT_RADIUS = 1_500
MAX_CITY_AREA = 20_000_000_000
POINT_TOLERANCE = 2_000
SETTLEMENT_TYPES = ("city", "town", "village", "municipality")
DISTRICT_TYPES = ("county", "municipality")
MAX_DISTRICT_AREA = 3_000_000_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MapJob:
    key: str
    entity_id: str
    output: pathlib.Path
    entity_name: str
    alpha_3: str | None
    place_name: str | None = None
    place_geometry: dict | None = None
    place_point: tuple[float, float] | None = None


class MapWorker:
    renderer: LocatorMap | None = None

    @classmethod
    def start(cls) -> None:
        cls.renderer = LocatorMap(NaturalEarth.load(), DetailLayers.load())

    @classmethod
    def render(cls, job: MapJob) -> str:
        data = cls.renderer.data
        country = data.shape_for(job.entity_name, job.alpha_3)
        if country is None:
            raise LookupError(f"No shape for {job.entity_name}")
        detail = cls.renderer.detail.country(job.entity_id) if cls.renderer.detail else None
        job.output.parent.mkdir(parents=True, exist_ok=True)

        if job.place_name is None:
            scene = Scene.for_country(country, Borders.COUNTRIES)
            if detail is not None:
                scene = replace(scene, lens_highlight=gpd.GeoSeries([detail], crs=data.crs))
        else:
            geometry = place_geometry(job, data.crs)
            if job.place_geometry is not None:
                geometry = clip_to(geometry, detail if detail is not None else land_of(country))
            place = Place(job.place_name, geometry, country, approximate=job.place_geometry is None)
            scene = Scene.for_place(place, Borders.REGIONS, Framing.COUNTRY)
            if detail is not None:
                scene = replace(scene, lens_host=gpd.GeoSeries([detail], crs=data.crs))
        cls.renderer.render(scene, job.output)
        return job.key


def local_projection(longitude: float, latitude: float) -> str:
    return f"+proj=aeqd +lat_0={latitude} +lon_0={longitude} +units=m"


def city_boundary(
    feature: dict | None,
    point: tuple[float, float] | None,
    max_area: float = MAX_CITY_AREA,
) -> dict | None:
    if not feature or feature["geometry"]["type"] not in AREA_TYPES:
        return None
    if point is None:
        return feature["geometry"]
    longitude, latitude = point
    local = local_projection(longitude, latitude)
    boundary = gpd.GeoSeries([shape(feature["geometry"])], crs="EPSG:4326").to_crs(local).iloc[0]
    center = gpd.GeoSeries([Point(longitude, latitude)], crs="EPSG:4326").to_crs(local).iloc[0]
    if boundary.area > max_area or boundary.distance(center) > POINT_TOLERANCE:
        return None
    return feature["geometry"]


def land_of(country: Country):
    return pd.concat([country.core.geometry, country.disputed.geometry]).union_all()


class OutsideCountryError(ValueError):
    pass


def clip_to(geometry: gpd.GeoSeries, land) -> gpd.GeoSeries:
    clipped = geometry.apply(simplify).intersection(land)
    if clipped.is_empty.all():
        raise OutsideCountryError("boundary has no land inside its country")
    return clipped


def place_geometry(job: MapJob, crs) -> gpd.GeoSeries:
    if job.place_geometry is not None:
        return gpd.GeoSeries([shape(job.place_geometry)], crs=crs)
    longitude, latitude = job.place_point
    point = gpd.GeoSeries([Point(longitude, latitude)], crs=crs)
    return point.to_crs(local_projection(longitude, latitude)).buffer(POINT_RADIUS).to_crs(crs)


def render_maps(jobs: Iterable[MapJob], workers: int) -> Iterable[tuple[MapJob, Exception | None]]:
    jobs = list(jobs)
    if not jobs:
        return
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context, initializer=MapWorker.start) as pool:
        futures = {pool.submit(MapWorker.render, job): job for job in jobs}
        for done, future in enumerate(as_completed(futures), start=1):
            job = futures[future]
            error = future.exception()
            if done % 25 == 0 or done == len(jobs):
                logger.info("Maps: %d/%d rendered", done, len(jobs))
            yield job, error
