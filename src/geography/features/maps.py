import functools
import logging
import multiprocessing
import pathlib
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace

import geopandas as gpd
from shapely import affinity
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Polygon, box

from geography.data import NaturalEarth
from geography.detail import DetailLayers
from geography.features.kinds import Kind, Shape
from geography.features.model import Feature
from geography.features.spatial import CRS
from geography.render import EQUAL_AREA, PROJECTABLE_LATITUDE, Framing, LocatorMap, Scene, local_projection
from geography.sources.natural_earth_physical import PhysicalLayer, load_layer

CONTEXT = 1.35
MIN_FRAME_M = 900_000
OUTLINE_SHARE = 0.0035
WATER_OUTLINE_SHARE = 0.0018
SIMPLIFY_SHARE = 0.004
SPECK_SHARE = 0.01
LINE_BAND_SHARE = 0.004
POINT_RADIUS_M = 3_000
POINT_LENS_SPAN_M = 60_000
BORDER_FRAME_M = 350_000
WATER_KINDS = {Kind.SEA, Kind.LAKE}
FRAME_SEGMENTS = 32
MAX_FRAME_WIDTH_M = 15_000_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureMapJob:
    key: str
    kind: Kind
    geometry: gpd.GeoSeries
    output: pathlib.Path
    host_country: str | None
    on_border: bool


def empty() -> gpd.GeoSeries:
    return gpd.GeoSeries([], crs=CRS)


def size_of(geometry: gpd.GeoSeries) -> float:
    minx, miny, maxx, maxy = geometry.to_crs(local_projection(geometry)).total_bounds
    return max(maxx - minx, maxy - miny)


def frame_around(geometry: gpd.GeoSeries, context: float = CONTEXT) -> gpd.GeoSeries:
    local = local_projection(geometry)
    projected = gpd.GeoSeries([box(*geometry.to_crs(local).total_bounds)], crs=local)
    size = size_of(geometry)
    factor = max(context, MIN_FRAME_M / size) if size else 1.0
    scaled = projected.apply(lambda envelope: affinity.scale(envelope, factor, factor))
    if not size:
        scaled = projected.centroid.buffer(MIN_FRAME_M / 2).envelope
    return scaled.segmentize(size * factor / FRAME_SEGMENTS if size else MIN_FRAME_M).to_crs(CRS)


def without_specks(shape, min_area: float):
    polygons = shape.geoms if isinstance(shape, MultiPolygon) else [shape]
    kept = [
        Polygon(polygon.exterior, [hole for hole in polygon.interiors if Polygon(hole).area >= min_area])
        for polygon in polygons
        if isinstance(polygon, Polygon) and polygon.area >= min_area
    ]
    return MultiPolygon(kept) if kept else shape


def outline(geometry: gpd.GeoSeries, share: float) -> gpd.GeoSeries:
    size = size_of(geometry)
    local = local_projection(geometry)
    projected = geometry.to_crs(local).apply(lambda shape: without_specks(shape, (size * SPECK_SHARE) ** 2))
    return projected.simplify(size * SIMPLIFY_SHARE).boundary.buffer(size * share).to_crs(CRS)


@functools.cache
def land():
    lakes = load_layer(PhysicalLayer.LAKES).geometry.make_valid()
    return NaturalEarth.load().countries.union_all().union(lakes.union_all())


def area_scene(job: FeatureMapJob) -> Scene:
    shape = job.geometry.union_all().buffer(0)
    if job.kind is Kind.SEA:
        shape = shape.difference(land())
    shape = gpd.GeoSeries([shape], crs=CRS)
    share = WATER_OUTLINE_SHARE if job.kind in WATER_KINDS else OUTLINE_SHARE
    return Scene(
        highlight=outline(shape, share),
        frame=frame_around(shape),
        disputed=empty(),
        host=shape,
        regions=NaturalEarth.load().countries.geometry,
        framing=Framing.COUNTRY,
        style=job.kind.spec.palette.style(Shape.AREA),
    )


def line_scene(job: FeatureMapJob) -> Scene:
    frame = frame_around(job.geometry)
    local = local_projection(job.geometry)
    band = job.geometry.to_crs(local).buffer(size_of(frame) * LINE_BAND_SHARE).to_crs(CRS)
    return Scene(
        highlight=band,
        frame=frame,
        disputed=empty(),
        host=empty(),
        regions=empty(),
        framing=Framing.COUNTRY,
        style=job.kind.spec.palette.style(Shape.LINE),
    )


def point_scene(job: FeatureMapJob, data: NaturalEarth) -> Scene:
    local = local_projection(job.geometry)
    spot = job.geometry.to_crs(local).buffer(POINT_RADIUS_M).to_crs(CRS)
    style = job.kind.spec.palette.style(Shape.POINT)
    country = data.shape_for("", job.host_country) if job.host_country and not job.on_border else None
    if country is None:
        frame = job.geometry.to_crs(local).buffer(BORDER_FRAME_M).to_crs(CRS)
        return Scene(highlight=spot, frame=frame, disputed=empty(), host=empty(), regions=data.countries.geometry,
                     min_zoom_span=POINT_LENS_SPAN_M, framing=Framing.COUNTRY, style=style)
    return Scene(highlight=spot, frame=country.core.geometry, disputed=empty(), host=country.core.geometry,
                 regions=empty(), min_zoom_span=POINT_LENS_SPAN_M, framing=Framing.COUNTRY, style=style)


def needs_world_view(geometry: gpd.GeoSeries, aspect_ratio: float) -> bool:
    minx, miny, maxx, maxy = geometry.total_bounds
    if max(-miny, maxy) > PROJECTABLE_LATITUDE or maxx - minx > 180:
        return True
    minx, miny, maxx, maxy = geometry.to_crs(local_projection(geometry)).total_bounds
    return max(maxx - minx, (maxy - miny) * aspect_ratio) * CONTEXT > MAX_FRAME_WIDTH_M


def line_centre(geometry: gpd.GeoSeries) -> float:
    line = geometry.union_all()
    near_greenwich = line.intersection(box(-90, -90, 90, 90)).length
    return 0.0 if near_greenwich >= line.length / 2 else 180.0


class FeatureMapWorker:
    renderer: LocatorMap | None = None

    @classmethod
    def start(cls) -> None:
        cls.renderer = LocatorMap(NaturalEarth.load(), DetailLayers.load())

    @classmethod
    def render(cls, job: FeatureMapJob) -> str:
        shape = job.kind.spec.shape
        job.output.parent.mkdir(parents=True, exist_ok=True)
        style = job.kind.spec.palette.style(shape)
        if shape is Shape.WORLD_AREA or (shape is Shape.AREA and needs_world_view(job.geometry, cls.renderer.aspect_ratio)):
            centre = job.geometry.to_crs(EQUAL_AREA).centroid.to_crs(CRS).iloc[0].x
            cls.renderer.render_world(job.output, style, centre, areas=job.geometry)
        elif shape is Shape.WORLD_LINE:
            centre = line_centre(job.geometry)
            cls.renderer.render_world(job.output, style, centre, lines=job.geometry)
        elif shape is Shape.POINT or job.geometry.geom_type.iloc[0] == "Point":
            try:
                cls.renderer.render(point_scene(job, cls.renderer.data), job.output)
            except (ValueError, TypeError, GEOSException):
                cls.renderer.render(point_scene(replace(job, on_border=True), cls.renderer.data), job.output)
        elif shape is Shape.LINE:
            cls.renderer.render(line_scene(job), job.output)
        else:
            try:
                cls.renderer.render(area_scene(job), job.output)
            except (ValueError, TypeError, GEOSException) as error:
                logger.warning("World view for %s: %s", job.key, error)
                centre = job.geometry.to_crs(EQUAL_AREA).centroid.to_crs(CRS).iloc[0].x
                cls.renderer.render_world(job.output, style, centre, areas=job.geometry)
        return job.key


def map_job(feature: Feature, output: pathlib.Path) -> FeatureMapJob:
    host = feature.countries[0].alpha_3 if feature.countries else None
    return FeatureMapJob(feature.key, feature.kind, feature.geometry, output, host, len(feature.countries) > 1)


def render_feature_maps(jobs: list[FeatureMapJob], workers: int) -> Iterable[tuple[FeatureMapJob, Exception | None]]:
    if not jobs:
        return
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context, initializer=FeatureMapWorker.start) as pool:
        futures = {pool.submit(FeatureMapWorker.render, job): job for job in jobs}
        for done, future in enumerate(as_completed(futures), start=1):
            if done % 25 == 0 or done == len(jobs):
                logger.info("Place maps: %d/%d rendered", done, len(jobs))
            yield futures[future], future.exception()
