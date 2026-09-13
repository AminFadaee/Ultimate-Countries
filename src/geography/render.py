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

from geography.data import Country, NaturalEarth
from geography.detail import DetailLayers
from geography.places import Place

GEODETIC = ccrs.PlateCarree()
EQUAL_AREA = "EPSG:6933"
METERS_PER_DEGREE = 111_320

FRAME_MARGIN_X = 25 * METERS_PER_DEGREE
FRAME_MARGIN_Y = 40 * METERS_PER_DEGREE
MIN_FRAME_HEIGHT = 10 * METERS_PER_DEGREE
MIN_INSET_BOX = 8 * METERS_PER_DEGREE

INSET_WIDTH = 0.26
INSET_MARGIN = 0.02

ZOOM_THRESHOLD = 0.02
LENS_PADDING = 3
LENS_RADIUS = 1.1
LENS_MARGIN = 0.25
MARKER_RADIUS = 0.12
MARKER_REACH = 1.4
MIN_POINT_LENS_SPAN = 40_000


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


class Borders(StrEnum):
    COUNTRIES = "countries"
    REGIONS = "regions"


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
    def for_place(cls, place: Place, borders: Borders) -> "Scene":
        country = place.country
        return cls(
            highlight=place.geometry,
            frame=country.core.geometry,
            disputed=empty_like(place.geometry),
            host=country.core.geometry,
            regions=region_borders(country, borders),
            min_zoom_span=MIN_POINT_LENS_SPAN if place.approximate else 0.0,
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


def get_center(mainland: gpd.GeoSeries) -> tuple[float, float]:
    centroid = mainland.to_crs(EQUAL_AREA).centroid.to_crs(mainland.crs).iloc[0]
    return centroid.y, centroid.x


def get_frame(mainland: gpd.GeoSeries, projection: ccrs.Projection, aspect_ratio: float) -> Extent:
    minx, miny, maxx, maxy = mainland.to_crs(projection).total_bounds
    height = max(pad(maxy - miny, FRAME_MARGIN_Y), MIN_FRAME_HEIGHT)
    width = max(pad(maxx - minx, FRAME_MARGIN_X), aspect_ratio * height)
    return Extent(width, height)


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
        extent = get_frame(mainland, projection, self.aspect_ratio)
        zoom = get_zoom(scene.lens_subject, projection, extent, scene.min_zoom_span)

        fig = plt.figure(figsize=(self.width, self.width * extent.height / extent.width))
        try:
            self._plot_main(fig, scene, projection, extent)
            self._plot_inset(fig, projection, extent)
            if zoom:
                self._plot_zoom(fig, scene, projection, extent, zoom)
            fig.savefig(out_path, dpi=self.dpi)
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
                facecolor=Color.HOST,
                edgecolor=Color.BORDER,
                linewidth=0.5,
                zorder=2,
            )
        ax.add_geometries(
            scene.highlight,
            crs=GEODETIC,
            facecolor=Color.HIGHLIGHT,
            edgecolor=Color.OUTLINE,
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
                facecolor=Color.HOST,
                edgecolor=Color.BORDER,
                linewidth=0.5,
                zorder=3,
            )
        ax.add_geometries(
            scene.highlight if scene.lens_highlight is None else scene.lens_highlight,
            crs=GEODETIC,
            facecolor=Color.HIGHLIGHT,
            edgecolor=Color.OUTLINE,
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

    def _plot_inset(self, fig: Figure, projection: ccrs.Projection, extent: Extent) -> None:
        fig_w, fig_h = fig.get_size_inches()
        height = INSET_WIDTH * 0.5 * fig_w / fig_h
        inset = fig.add_axes([INSET_MARGIN, INSET_MARGIN, INSET_WIDTH, height], projection=GEODETIC)
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
        lens = Circle(fig_w - LENS_MARGIN - LENS_RADIUS, fig_h - LENS_MARGIN - LENS_RADIUS, LENS_RADIUS)

        x, y, w, h = lens.rect
        ax = fig.add_axes([x / fig_w, y / fig_h, w / fig_w, h / fig_h], projection=projection)
        ax.set_boundary(flatten(Path.circle((0.5, 0.5), 0.5)), transform=ax.transAxes)
        ax.spines["geo"].set_edgecolor(Color.HIGHLIGHT)
        ax.spines["geo"].set_linewidth(1.2)
        if self.detail is None:
            self._draw_layers(ax, scene, projection, zoom.lens)
        else:
            self._draw_detail(ax, scene, projection, zoom.lens)

        style = {"color": Color.HIGHLIGHT, "linewidth": 1.2, "transform": fig.dpi_scale_trans}
        fig.add_artist(patches.Circle((marker.x, marker.y), marker.radius, fill=False, **style))
        for xs, ys in marker.tangents_to(lens):
            fig.add_artist(Line2D(xs, ys, **style))
