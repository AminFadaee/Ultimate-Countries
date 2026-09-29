import io
import math
import pathlib
from dataclasses import dataclass
from enum import StrEnum

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import patches
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.path import Path
from shapely.geometry import box
from shapely.ops import transform

from geography.data import Country, NaturalEarth
from geography.detail import DetailLayers
from geography.images import save_compact
from geography.places import Place

GEODETIC = ccrs.PlateCarree()
EQUAL_AREA = "EPSG:6933"
METERS_PER_DEGREE = 111_320

FRAME_MARGIN_X = 25 * METERS_PER_DEGREE
FRAME_MARGIN_Y = 40 * METERS_PER_DEGREE
MIN_FRAME_HEIGHT = 10 * METERS_PER_DEGREE
COUNTRY_FRAME_MARGIN = 0.12
MIN_COUNTRY_FRAME_HEIGHT = 1.5 * METERS_PER_DEGREE
NEARBY_PART_DISTANCE = 300_000
MIN_INSET_BOX = 8 * METERS_PER_DEGREE

INSET_WIDTH = 0.26
INSET_MARGIN = 0.02

ZOOM_THRESHOLD = 0.02
LENS_PADDING = 3
LENS_RADIUS = 1.1
LENS_MARGIN = 0.25
MARKER_RADIUS = 0.12
LENS_CLEARANCE = 0.1
MARKER_REACH = 1.4
MIN_POINT_LENS_SPAN = 40_000
WORLD_SIZE = (10, 5.26)
WORLD_SIMPLIFY = 0.05
SEAM_CLOSING = 0.2
WORLD_OUTLINE_WIDTH = 1.6
WORLD_LINE_WIDTH = 2.4


class Color(StrEnum):
    OCEAN = "#AEDFF7"
    LAND = "#e9dfc7"
    BORDER = "#353535"
    REGION_BORDER = "#7a7a7a"
    HOST = "#e8c3b9"
    HIGHLIGHT = "#A6192E"
    DISPUTED = "#ca5e6e"
    OUTLINE = "black"
    INSET_LAND = "#C5C5C5"
    INSET_BACKGROUND = "white"
    INSET_BOX = "red"


class Corner(StrEnum):
    TOP_RIGHT = "top right"
    TOP_LEFT = "top left"
    BOTTOM_RIGHT = "bottom right"
    BOTTOM_LEFT = "bottom left"

    @property
    def is_left(self) -> bool:
        return self in (Corner.TOP_LEFT, Corner.BOTTOM_LEFT)

    @property
    def is_bottom(self) -> bool:
        return self in (Corner.BOTTOM_LEFT, Corner.BOTTOM_RIGHT)


INSET_CORNERS = (Corner.BOTTOM_LEFT, Corner.BOTTOM_RIGHT, Corner.TOP_LEFT)
LENS_CORNERS = (Corner.TOP_RIGHT, Corner.TOP_LEFT, Corner.BOTTOM_RIGHT, Corner.BOTTOM_LEFT)


class Framing(StrEnum):
    REGION = "region"
    COUNTRY = "country"


class Borders(StrEnum):
    COUNTRIES = "countries"
    REGIONS = "regions"


@dataclass(frozen=True)
class Style:
    highlight: str = Color.HIGHLIGHT
    host: str = Color.HOST
    outline: str = Color.OUTLINE


@dataclass(frozen=True)
class Scene:
    highlight: gpd.GeoSeries
    frame: gpd.GeoSeries
    disputed: gpd.GeoSeries
    host: gpd.GeoSeries
    regions: gpd.GeoSeries
    min_zoom_span: float = 0.0
    lens_highlight: gpd.GeoSeries | None = None
    lens_host: gpd.GeoSeries | None = None
    framing: Framing = Framing.REGION
    style: Style = Style()

    @classmethod
    def for_country(cls, country: Country, borders: Borders) -> "Scene":
        return cls(
            highlight=country.core.geometry,
            frame=country.disputed.geometry if country.core.empty else country.core.geometry,
            disputed=country.disputed.geometry,
            host=empty_like(country.core.geometry),
            regions=region_borders(country, borders),
        )

    @classmethod
    def for_place(cls, place: Place, borders: Borders, framing: Framing = Framing.REGION) -> "Scene":
        country = place.country
        return cls(
            highlight=place.geometry,
            frame=country.core.geometry,
            disputed=empty_like(place.geometry),
            host=country.core.geometry,
            regions=region_borders(country, borders),
            min_zoom_span=MIN_POINT_LENS_SPAN if place.approximate else 0.0,
            framing=framing,
        )

    @property
    def subject(self) -> gpd.GeoSeries:
        return pd.concat([self.highlight, self.disputed])

    @property
    def lens_subject(self) -> gpd.GeoSeries:
        return self.subject if self.lens_highlight is None else self.lens_highlight


@dataclass(frozen=True)
class Extent:
    width: float
    height: float
    x: float = 0.0
    y: float = 0.0

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (
            self.x - self.width / 2,
            self.x + self.width / 2,
            self.y - self.height / 2,
            self.y + self.height / 2,
        )

    @property
    def box(self):
        xmin, xmax, ymin, ymax = self.bounds
        return box(xmin, ymin, xmax, ymax)

    def at_least(self, size: float) -> "Extent":
        return Extent(max(self.width, size), max(self.height, size), self.x, self.y)


@dataclass(frozen=True)
class Zoom:
    lens: Extent
    subject_size: float


@dataclass(frozen=True)
class Circle:
    x: float
    y: float
    radius: float

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return (self.x - self.radius, self.y - self.radius, 2 * self.radius, 2 * self.radius)

    def distance_to(self, other: "Circle") -> float:
        return math.hypot(other.x - self.x, other.y - self.y)

    def is_clear_of(self, other: "Circle") -> bool:
        return self.distance_to(other) > self.radius + other.radius + LENS_CLEARANCE

    def tangents_to(self, other: "Circle") -> list[tuple[list[float], list[float]]]:
        dx, dy = other.x - self.x, other.y - self.y
        base = math.atan2(dy, dx)
        offset = math.acos((self.radius - other.radius) / math.hypot(dx, dy))
        lines = []
        for angle in (base + offset, base - offset):
            nx, ny = math.cos(angle), math.sin(angle)
            lines.append(
                (
                    [self.x + nx * self.radius, other.x + nx * other.radius],
                    [self.y + ny * self.radius, other.y + ny * other.radius],
                )
            )
        return lines


def empty_like(series: gpd.GeoSeries) -> gpd.GeoSeries:
    return gpd.GeoSeries([], crs=series.crs)


def region_borders(country: Country, borders: Borders) -> gpd.GeoSeries:
    if borders is Borders.REGIONS:
        return country.regions.geometry
    return empty_like(country.regions.geometry)


def pad(size: float, margin: float) -> float:
    return size + margin * min(1, size / METERS_PER_DEGREE)


def flatten(path: Path, steps: int = 32) -> Path:
    t = np.linspace(0, 1, steps)
    return Path(np.concatenate([curve(t) for curve, _ in path.iter_bezier()]))


def get_mainland(geometry: gpd.GeoSeries) -> gpd.GeoSeries:
    parts = geometry.explode(index_parts=False)
    return parts.iloc[[parts.to_crs(EQUAL_AREA).area.argmax()]]


def lens_at(corner: Corner, fig_w: float, fig_h: float) -> "Circle":
    x = LENS_MARGIN + LENS_RADIUS if corner.is_left else fig_w - LENS_MARGIN - LENS_RADIUS
    y = LENS_MARGIN + LENS_RADIUS if corner.is_bottom else fig_h - LENS_MARGIN - LENS_RADIUS
    return Circle(x, y, LENS_RADIUS)


def place_lens(marker: "Circle", fig_w: float, fig_h: float, taken: Corner) -> "Circle":
    lenses = [lens_at(corner, fig_w, fig_h) for corner in LENS_CORNERS if corner is not taken]
    clear = [lens for lens in lenses if lens.is_clear_of(marker)]
    return clear[0] if clear else max(lenses, key=lambda lens: lens.distance_to(marker))


def inset_rect(corner: Corner, aspect: float) -> tuple[float, float, float, float]:
    height = INSET_WIDTH * 0.5 * aspect
    x = INSET_MARGIN if corner.is_left else 1 - INSET_MARGIN - INSET_WIDTH
    y = INSET_MARGIN if corner.is_bottom else 1 - INSET_MARGIN - height
    return x, y, INSET_WIDTH, height


def place_inset(subject: tuple[float, float], aspect: float) -> Corner:
    def covers(corner: Corner) -> bool:
        x, y, width, height = inset_rect(corner, aspect)
        return x - INSET_MARGIN <= subject[0] <= x + width + INSET_MARGIN and y - INSET_MARGIN <= subject[1] <= y + height + INSET_MARGIN

    return next((corner for corner in INSET_CORNERS if not covers(corner)), INSET_CORNERS[0])


def get_frame_area(scene: "Scene", mainland: gpd.GeoSeries, projection: ccrs.Projection) -> tuple[gpd.GeoSeries, Framing]:
    if scene.framing is Framing.REGION:
        return mainland, Framing.REGION
    country = get_nearby_parts(scene.frame, mainland)
    minx, miny, maxx, maxy = country.to_crs(projection).total_bounds
    if max(maxx - minx, maxy - miny) < MIN_COUNTRY_FRAME_HEIGHT:
        return mainland, Framing.REGION
    return pd.concat([country, get_mainland(scene.highlight)]), Framing.COUNTRY


def figure_position(geometry: gpd.GeoSeries, projection: ccrs.Projection, extent: Extent) -> tuple[float, float]:
    minx, miny, maxx, maxy = geometry.to_crs(projection).total_bounds
    xmin, _, ymin, _ = extent.bounds
    return ((minx + maxx) / 2 - xmin) / extent.width, ((miny + maxy) / 2 - ymin) / extent.height


def local_projection(mainland: gpd.GeoSeries) -> str:
    lat, lng = get_center(mainland)
    return f"+proj=aeqd +lat_0={lat} +lon_0={lng} +units=m"


def get_nearby_parts(geometry: gpd.GeoSeries, mainland: gpd.GeoSeries) -> gpd.GeoSeries:
    parts = geometry.explode(index_parts=False)
    local = local_projection(mainland)
    distances = parts.to_crs(local).distance(mainland.to_crs(local).iloc[0])
    return parts[distances <= NEARBY_PART_DISTANCE]


def get_center(mainland: gpd.GeoSeries) -> tuple[float, float]:
    centroid = mainland.to_crs(EQUAL_AREA).centroid.to_crs(mainland.crs).iloc[0]
    return centroid.y, centroid.x


def get_frame(area: gpd.GeoSeries, projection: ccrs.Projection, aspect_ratio: float, framing: Framing) -> Extent:
    minx, miny, maxx, maxy = area.to_crs(projection).total_bounds
    if framing is Framing.REGION:
        height = max(pad(maxy - miny, FRAME_MARGIN_Y), MIN_FRAME_HEIGHT)
        width = max(pad(maxx - minx, FRAME_MARGIN_X), aspect_ratio * height)
        return Extent(width, height)
    height = max((maxy - miny) * (1 + 2 * COUNTRY_FRAME_MARGIN), MIN_COUNTRY_FRAME_HEIGHT)
    width = max((maxx - minx) * (1 + 2 * COUNTRY_FRAME_MARGIN), aspect_ratio * height)
    return Extent(width, height, (minx + maxx) / 2, (miny + maxy) / 2)


def get_zoom(
    geometry: gpd.GeoSeries,
    projection: ccrs.Projection,
    extent: Extent,
    min_span: float = 0.0,
) -> Zoom | None:
    minx, miny, maxx, maxy = geometry.to_crs(projection).total_bounds
    size = max(maxx - minx, maxy - miny)
    if size >= ZOOM_THRESHOLD * extent.width:
        return None
    span = max(size * LENS_PADDING, min_span)
    return Zoom(Extent(span, span, (minx + maxx) / 2, (miny + maxy) / 2), size)


def recentred(geometry, centre: float):
    return transform(lambda x, y, z=None: (((x - centre + 180) % 360) - 180, y), geometry)


def seamless(areas: gpd.GeoSeries, centre: float):
    shifted = gpd.GeoSeries([recentred(area.simplify(WORLD_SIMPLIFY), centre) for area in areas.to_crs(GEODETIC.proj4_init)])
    return shifted.buffer(SEAM_CLOSING).union_all().buffer(-SEAM_CLOSING)


def get_visible(features: gpd.GeoSeries, projection: ccrs.Projection, extent: Extent) -> gpd.GeoSeries:
    view = GEODETIC.project_geometry(extent.box, projection)
    return features[features.intersects(view)]


class LocatorMap:
    def __init__(
        self,
        data: NaturalEarth,
        detail: DetailLayers | None = None,
        aspect_ratio: float = 1.9,
        width: float = 10,
        dpi: int = 200,
    ):
        self.data = data
        self.detail = detail
        self.aspect_ratio = aspect_ratio
        self.width = width
        self.dpi = dpi

    def render_place(
        self,
        place: Place,
        out_path: pathlib.Path,
        borders: Borders = Borders.COUNTRIES,
    ) -> None:
        self.render(Scene.for_place(place, borders), out_path)

    def render(self, scene: Scene, out_path: pathlib.Path) -> None:
        mainland = get_mainland(scene.frame)
        lat, lng = get_center(mainland)
        projection = ccrs.LambertAzimuthalEqualArea(central_longitude=lng, central_latitude=lat)
        area, framing = get_frame_area(scene, mainland, projection)
        extent = get_frame(area, projection, self.aspect_ratio, framing)
        zoom = get_zoom(scene.lens_subject, projection, extent, scene.min_zoom_span)
        inset_corner = place_inset(figure_position(scene.lens_subject, projection, extent), extent.width / extent.height)

        fig = plt.figure(figsize=(self.width, self.width * extent.height / extent.width))
        try:
            self._plot_main(fig, scene, projection, extent)
            self._plot_inset(fig, projection, extent, inset_corner)
            if zoom:
                self._plot_zoom(fig, scene, projection, extent, zoom, inset_corner)
            rendered = io.BytesIO()
            fig.savefig(rendered, dpi=self.dpi, format="png")
            rendered.seek(0)
            save_compact(rendered, out_path)
        finally:
            plt.close(fig)

    def render_world(
        self,
        out_path: pathlib.Path,
        style: Style,
        centre: float = 0.0,
        areas: gpd.GeoSeries | None = None,
        lines: gpd.GeoSeries | None = None,
    ) -> None:
        fig = plt.figure(figsize=WORLD_SIZE)
        try:
            ax = fig.add_axes([0, 0, 1, 1], projection=ccrs.Robinson(central_longitude=centre))
            ax.set_global()
            ax.set_facecolor(Color.OCEAN)
            ax.spines["geo"].set_visible(False)
            shifted = ccrs.PlateCarree(central_longitude=centre)
            merged = seamless(areas, centre) if areas is not None else None
            if merged is not None:
                ax.add_geometries([merged], crs=shifted, facecolor=style.host, edgecolor="none")
            ax.add_geometries(
                self.data.countries.geometry, crs=GEODETIC, facecolor=Color.LAND, edgecolor=Color.BORDER, linewidth=0.3
            )
            if merged is not None:
                ax.add_geometries(
                    [merged.boundary], crs=shifted, facecolor="none", edgecolor=style.highlight,
                    linewidth=WORLD_OUTLINE_WIDTH, zorder=6,
                )
            if lines is not None:
                ax.add_geometries(
                    lines.to_crs(GEODETIC.proj4_init), crs=GEODETIC, facecolor="none", edgecolor=style.highlight,
                    linewidth=WORLD_LINE_WIDTH, zorder=6,
                )
            rendered = io.BytesIO()
            fig.savefig(rendered, dpi=self.dpi, format="png")
            rendered.seek(0)
            save_compact(rendered, out_path)
        finally:
            plt.close(fig)

    def _draw_layers(self, ax, scene: Scene, projection: ccrs.Projection, extent: Extent) -> None:
        ax.set_extent(extent.bounds, crs=projection)
        ax.set_facecolor(Color.OCEAN)
        ax.add_geometries(
            get_visible(self.data.countries.geometry, projection, extent),
            crs=GEODETIC,
            facecolor=Color.LAND,
            edgecolor=Color.BORDER,
            linewidth=0.4,
        )
        if not scene.host.empty:
            ax.add_geometries(
                scene.host,
                crs=GEODETIC,
                facecolor=scene.style.host,
                edgecolor=Color.BORDER,
                linewidth=0.5,
                zorder=2,
            )
        ax.add_geometries(
            scene.highlight,
            crs=GEODETIC,
            facecolor=scene.style.highlight,
            edgecolor=scene.style.outline,
            linewidth=0.8,
            zorder=3,
        )
        if not scene.disputed.empty:
            ax.add_geometries(
                scene.disputed,
                crs=GEODETIC,
                facecolor=Color.DISPUTED,
                edgecolor=Color.OUTLINE,
                linewidth=0.7,
                zorder=4,
            )
        if not scene.regions.empty:
            ax.add_geometries(
                get_visible(scene.regions, projection, extent),
                crs=GEODETIC,
                facecolor="none",
                edgecolor=Color.REGION_BORDER,
                linewidth=0.3,
                zorder=5,
            )

    def _draw_detail(self, ax, scene: Scene, projection: ccrs.Projection, extent: Extent) -> None:
        ax.set_extent(extent.bounds, crs=projection)
        ax.set_facecolor(Color.OCEAN)
        view = GEODETIC.project_geometry(extent.box, projection)
        ax.add_geometries(
            self.detail.land_in(view),
            crs=GEODETIC,
            facecolor=Color.LAND,
            edgecolor=Color.BORDER,
            linewidth=0.4,
        )
        ax.add_geometries(
            self.detail.borders_in(view),
            crs=GEODETIC,
            facecolor="none",
            edgecolor=Color.BORDER,
            linewidth=0.4,
            zorder=2,
        )
        if scene.lens_host is not None:
            ax.add_geometries(
                scene.lens_host,
                crs=GEODETIC,
                facecolor=scene.style.host,
                edgecolor=Color.BORDER,
                linewidth=0.5,
                zorder=3,
            )
        ax.add_geometries(
            scene.highlight if scene.lens_highlight is None else scene.lens_highlight,
            crs=GEODETIC,
            facecolor=scene.style.highlight,
            edgecolor=scene.style.outline,
            linewidth=0.8,
            zorder=4,
        )
        ax.add_geometries(
            self.detail.lakes_in(view),
            crs=GEODETIC,
            facecolor=Color.OCEAN,
            edgecolor=Color.BORDER,
            linewidth=0.4,
            zorder=5,
        )

    def _plot_main(self, fig: Figure, scene: Scene, projection: ccrs.Projection, extent: Extent) -> None:
        ax = fig.add_axes([0, 0, 1, 1], projection=projection)
        ax.spines["geo"].set_visible(False)
        self._draw_layers(ax, scene, projection, extent)

    def _plot_inset(self, fig: Figure, projection: ccrs.Projection, extent: Extent, corner: Corner) -> None:
        fig_w, fig_h = fig.get_size_inches()
        inset = fig.add_axes(list(inset_rect(corner, fig_w / fig_h)), projection=GEODETIC)
        inset.set_global()

        frame = patches.FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.02,rounding_size=0.52")
        inset.set_boundary(flatten(frame.get_path()), transform=inset.transAxes)
        inset.set_facecolor(Color.INSET_BACKGROUND)
        inset.spines["geo"].set_linewidth(0.5)

        inset.add_geometries(
            self.data.countries.geometry,
            crs=GEODETIC,
            facecolor=Color.INSET_LAND,
            edgecolor=Color.INSET_LAND,
            linewidth=0,
        )
        inset.add_geometries(
            [extent.at_least(MIN_INSET_BOX).box],
            crs=projection,
            facecolor="none",
            edgecolor=Color.INSET_BOX,
            linewidth=1.3,
            zorder=5,
        )

    def _plot_zoom(
        self,
        fig: Figure,
        scene: Scene,
        projection: ccrs.Projection,
        extent: Extent,
        zoom: Zoom,
        inset_corner: Corner,
    ) -> None:
        fig_w, fig_h = fig.get_size_inches()
        xmin, _, ymin, _ = extent.bounds
        scale = fig_w / extent.width
        reach = zoom.subject_size / 2 * scale
        marker = Circle(
            (zoom.lens.x - xmin) * scale,
            (zoom.lens.y - ymin) * scale,
            max(MARKER_RADIUS, MARKER_REACH * reach),
        )
        lens = place_lens(marker, fig_w, fig_h, inset_corner)

        x, y, w, h = lens.rect
        ax = fig.add_axes([x / fig_w, y / fig_h, w / fig_w, h / fig_h], projection=projection)
        ax.set_boundary(flatten(Path.circle((0.5, 0.5), 0.5)), transform=ax.transAxes)
        ax.spines["geo"].set_edgecolor(scene.style.highlight)
        ax.spines["geo"].set_linewidth(1.2)
        if self.detail is None:
            self._draw_layers(ax, scene, projection, zoom.lens)
        else:
            self._draw_detail(ax, scene, projection, zoom.lens)

        style = {"color": scene.style.highlight, "linewidth": 1.2, "transform": fig.dpi_scale_trans}
        fig.add_artist(patches.Circle((marker.x, marker.y), marker.radius, fill=False, **style))
        for xs, ys in marker.tangents_to(lens):
            fig.add_artist(Line2D(xs, ys, **style))
