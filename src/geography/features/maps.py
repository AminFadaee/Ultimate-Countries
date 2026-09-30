import functools
import logging
import multiprocessing
import pathlib
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from enum import StrEnum

import cartopy.crs as ccrs
import geopandas as gpd
import numpy as np
import shapely
from shapely import affinity
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Polygon, box

from geography.data import NaturalEarth
from geography.detail import DetailLayers
from geography.features.kinds import WATER_KINDS, Kind, Shape
from geography.features.model import Feature
from geography.features.spatial import CRS
from geography.render import (
    EQUAL_AREA,
    PROJECTABLE_LATITUDE,
    CentredScene,
    Extent,
    Framing,
    LocatorMap,
    Scene,
    local_projection,
)
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
FRAME_SEGMENTS = 32
MAX_FRAME_WIDTH_M = 15_000_000
MIN_CONTEXT = 1.05
CONTEXT_STEP = 0.05
HEMISPHERE_M = 8_500_000
POLAR_RING_LATITUDE = 60
ANTIPODE_CLEARANCE_DEG = 30
SEAM_CLOSING_M = 1_000
POLAR_VIEW_LATITUDE = 45
EARTH_RADIUS_M = 6_371_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureMapJob:
    key: str
    kind: Kind
    geometry: gpd.GeoSeries
    output: pathlib.Path
    host_country: str | None
    on_border: bool


def is_line(job: FeatureMapJob) -> bool:
    return job.kind.spec.shape.is_line


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


def without_small_parts(projected: gpd.GeoSeries, size: float) -> gpd.GeoSeries:
    return projected.apply(lambda shape: without_specks(shape, (size * SPECK_SHARE) ** 2))


def outline_band(projected: gpd.GeoSeries, size: float, kind: Kind) -> gpd.GeoSeries:
    share = WATER_OUTLINE_SHARE if kind in WATER_KINDS else OUTLINE_SHARE
    return projected.simplify(size * SIMPLIFY_SHARE).boundary.buffer(size * share)


def outline(geometry: gpd.GeoSeries, kind: Kind) -> gpd.GeoSeries:
    size = size_of(geometry)
    projected = without_small_parts(geometry.to_crs(local_projection(geometry)), size)
    return outline_band(projected, size, kind).to_crs(CRS)


@functools.cache
def land():
    lakes = load_layer(PhysicalLayer.LAKES).geometry.make_valid()
    return NaturalEarth.load().countries.union_all().union(lakes.union_all())


def drawn_shape(job: FeatureMapJob):
    shape = job.geometry.union_all()
    if is_line(job):
        return shape
    shape = shape.buffer(0)
    return shape.difference(land()) if job.kind is Kind.SEA else shape


def area_scene(job: FeatureMapJob) -> Scene:
    shape = gpd.GeoSeries([drawn_shape(job)], crs=CRS)
    return Scene(
        highlight=outline(shape, job.kind),
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


class View(StrEnum):
    REGIONAL = "regional"
    CENTRED = "centred"
    WORLD = "world"


def shifted_east(shape):
    return shapely.transform(shape, lambda xy: np.column_stack([np.where(xy[:, 0] < 0, xy[:, 0] + 360, xy[:, 0]), xy[:, 1]]))


def longitude_span(geometry: gpd.GeoSeries) -> tuple[float, float]:
    minx, _, maxx, _ = geometry.total_bounds
    east_minx, _, east_maxx, _ = shifted_east(geometry.union_all()).bounds
    return maxx - minx, east_maxx - east_minx


def pole_of(geometry: gpd.GeoSeries) -> float | None:
    _, miny, _, maxy = geometry.total_bounds
    around_pole = min(longitude_span(geometry)) > 180
    north = maxy > PROJECTABLE_LATITUDE or (around_pole and miny > POLAR_RING_LATITUDE)
    south = miny < -PROJECTABLE_LATITUDE or (around_pole and maxy < -POLAR_RING_LATITUDE)
    if north == south:
        return None
    return 90.0 if north else -90.0


def crosses_date_line(geometry: gpd.GeoSeries) -> bool:
    span, east_span = longitude_span(geometry)
    return span > 180 >= east_span


def too_wide_for_regional(geometry: gpd.GeoSeries, aspect_ratio: float) -> bool:
    minx, miny, maxx, maxy = geometry.to_crs(local_projection(geometry)).total_bounds
    return max(maxx - minx, (maxy - miny) * aspect_ratio) * CONTEXT > MAX_FRAME_WIDTH_M


def centre_of(geometry: gpd.GeoSeries) -> tuple[float, float]:
    if (pole := pole_of(geometry)) is not None:
        return pole, 0.0
    shape = geometry.union_all()
    if crosses_date_line(geometry):
        shape = shifted_east(shape)
    minx, miny, maxx, maxy = shape.bounds
    return (miny + maxy) / 2, ((minx + maxx) / 2 + 180) % 360 - 180


def view_for(job: FeatureMapJob, aspect_ratio: float) -> View:
    shape = job.kind.spec.shape
    if shape in (Shape.POINT, Shape.LINE) or job.geometry.geom_type.iloc[0] == "Point":
        return View.REGIONAL
    if pole_of(job.geometry) is not None:
        return View.CENTRED
    if shape in (Shape.WORLD_AREA, Shape.WORLD_LINE):
        return View.WORLD
    if crosses_date_line(job.geometry) or too_wide_for_regional(job.geometry, aspect_ratio):
        return View.CENTRED
    return View.REGIONAL


def angular_distance(coordinates: np.ndarray, latitude: float, longitude: float) -> np.ndarray:
    lon, lat = np.radians(coordinates[:, 0]), np.radians(coordinates[:, 1])
    lat0, lon0 = np.radians(latitude), np.radians(longitude)
    cosine = np.sin(lat) * np.sin(lat0) + np.cos(lat) * np.cos(lat0) * np.cos(lon - lon0)
    return np.degrees(np.arccos(np.clip(cosine, -1, 1)))


def facing(countries: gpd.GeoSeries, latitude: float, longitude: float) -> gpd.GeoSeries:
    antipode = -latitude, (longitude + 360) % 360 - 180
    clear = [angular_distance(shapely.get_coordinates(country), *antipode).min() > ANTIPODE_CLEARANCE_DEG for country in countries]
    return countries[clear]


def closed_seams(projected: gpd.GeoSeries) -> gpd.GeoSeries:
    return projected.buffer(SEAM_CLOSING_M).buffer(-SEAM_CLOSING_M)


def reach_of(latitude: float) -> float:
    return 2 * EARTH_RADIUS_M * np.sin(np.radians(90 - abs(latitude)) / 2)


def fitted_extent(projected: gpd.GeoSeries, aspect_ratio: float, polar: bool) -> Extent | None:
    minx, miny, maxx, maxy = projected.total_bounds
    width = max(maxx - minx, (maxy - miny) * aspect_ratio, MIN_FRAME_M)
    if polar:
        width = max(width * CONTEXT, 2 * reach_of(POLAR_VIEW_LATITUDE) * aspect_ratio)
        return Extent(width, width / aspect_ratio)
    centre_x, centre_y = (minx + maxx) / 2, (miny + maxy) / 2
    context = CONTEXT
    while context >= MIN_CONTEXT:
        extent = Extent(width * context, width * context / aspect_ratio, centre_x, centre_y)
        xmin, xmax, ymin, ymax = extent.bounds
        if max(abs(xmin), abs(xmax)) ** 2 + max(abs(ymin), abs(ymax)) ** 2 <= HEMISPHERE_M ** 2:
            return extent
        context = round(context - CONTEXT_STEP, 2)
    return None


def centred_scene(job: FeatureMapJob, data: NaturalEarth, aspect_ratio: float) -> CentredScene | None:
    latitude, longitude = centre_of(job.geometry)
    projection = ccrs.LambertAzimuthalEqualArea(central_longitude=longitude, central_latitude=latitude)
    projected = gpd.GeoSeries([drawn_shape(job)], crs=CRS).to_crs(projection.proj4_init)
    polar = pole_of(job.geometry) is not None
    extent = fitted_extent(projected, aspect_ratio, polar)
    if extent is None:
        return None
    size = max(extent.width, extent.height) / CONTEXT
    style = job.kind.spec.palette.style(job.kind.spec.shape)
    if is_line(job):
        host, highlight = empty(), projected.buffer(size * LINE_BAND_SHARE)
    else:
        host = without_small_parts(closed_seams(projected), size)
        highlight = outline_band(host, size, job.kind)
    countries = closed_seams(facing(data.countries.geometry, latitude, longitude).to_crs(projection.proj4_init))
    return CentredScene(projection, extent, host, highlight, countries, style, inset_shape=job.geometry if polar else None)


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
        view = view_for(job, cls.renderer.aspect_ratio)
        if view is View.CENTRED and (scene := centred_scene(job, cls.renderer.data, cls.renderer.aspect_ratio)):
            cls.renderer.render_centred(scene, job.output)
        elif view is not View.REGIONAL:
            cls._render_world(job)
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
                cls._render_world(job)
        return job.key

    @classmethod
    def _render_world(cls, job: FeatureMapJob) -> None:
        style = job.kind.spec.palette.style(job.kind.spec.shape)
        if is_line(job):
            cls.renderer.render_world(job.output, style, line_centre(job.geometry), lines=job.geometry)
        else:
            centre = job.geometry.to_crs(EQUAL_AREA).centroid.to_crs(CRS).iloc[0].x
            cls.renderer.render_world(job.output, style, centre, areas=job.geometry)


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
