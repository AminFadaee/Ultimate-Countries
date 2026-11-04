import math

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.patheffects as effects
import numpy as np
from matplotlib import patches
from shapely.geometry import Point
from shapely.ops import nearest_points

from geography.features.kinds import Kind
from geography.features.riverart import icons
from geography.features.surroundings import Surroundings, Waterway
from geography.features.water_labels import WaterLabeller
from geography.render import Extent, LocatorMap

GEODETIC = ccrs.PlateCarree()
RIVER_GREY = "#8a9099"
RIVER_TEXT = "#4d5560"
LAND_HALO = "#e9dfc7"
RIVER_WIDTH, RIVER_EDGE = 2.0, 3.0
RIVER_ZORDER = 2.6
ICONS_ON_TOP = 8.5
LANDMARK_ICONS = {Kind.WATERFALL: (icons.waterfall, 28, 0.62), Kind.CANYON: (icons.gorge, 24, 0.94)}
ICON_SHARE_OF_MARKER = 0.75
MIN_ICON_PT = 20
NAME_SIZE = 8
NAME_GAPS_PT = (45, 70, 30, 100, 140)
SAMPLES = 400


class SurroundingsPainter:
    def __init__(self, surroundings: Surroundings, feature: gpd.GeoSeries, renderer: LocatorMap, fig, projection: ccrs.Projection, extent):
        self.surroundings, self.renderer, self.fig, self.projection, self.extent = surroundings, renderer, fig, projection, extent
        self.feature = feature.union_all()
        self.rivers = [(river, outside(river.shape, self.feature)) for river in surroundings.rivers]
        self.ax = fig.axes[0]
        self.unit = extent.width / (fig.get_size_inches()[0] * 72)
        self.render = fig.canvas.get_renderer()

    def paint(self) -> None:
        for _, lines in self.rivers:
            draw_river(self.ax, lines)
        if self.surroundings.landmark in LANDMARK_ICONS:
            self._landmark()
        for river, lines in self.rivers:
            self._river_name(river.name, lines)
        if self.surroundings.waters:
            labeller = WaterLabeller(self.ax, self.projection, self.extent, self.unit, self.renderer.data.countries.geometry.union_all())
            for water in self.surroundings.waters:
                self._water_name(labeller, water)

    def _landmark(self) -> None:
        draw, size, height = LANDMARK_ICONS[self.surroundings.landmark]
        spot = self.feature.representative_point()
        x, y = self.projection.transform_point(spot.x, spot.y, GEODETIC)
        base = y - height * size * self.unit / 2
        if (marker := self._marker(x, y)) is not None:
            (cx, cy), radius_pt = marker
            x, y = self.ax.transData.inverted().transform((cx, cy))
            if ICON_SHARE_OF_MARKER * 2 * radius_pt >= MIN_ICON_PT:
                size = min(size, ICON_SHARE_OF_MARKER * 2 * radius_pt / height)
                base = y - height * size * self.unit / 2
            else:
                base = y - (radius_pt + height * size) * self.unit
        draw(self.ax, x, base, size * self.unit, zorder=ICONS_ON_TOP)

    def _marker(self, x: float, y: float):
        spot = self.ax.transData.transform((x, y))
        circles = [artist for artist in self.fig.artists if isinstance(artist, patches.Circle)]
        found = []
        for circle in circles:
            centre = np.array(circle.center) * self.fig.dpi
            radius = circle.radius * self.fig.dpi
            if np.linalg.norm(centre - spot) <= radius:
                found.append((radius, (tuple(centre), circle.radius * 72)))
        return min(found)[1] if found else None

    def _display(self, x: float, y: float) -> np.ndarray:
        return self.ax.transData.transform(self.projection.transform_point(x, y, GEODETIC))

    def _river_name(self, name: str, lines: gpd.GeoSeries) -> None:
        if lines.empty:
            return
        line = lines.union_all()
        anchor = self._display(*nearest_points(self.feature, line)[1].coords[0])
        points = [line.interpolate(step / SAMPLES, normalized=True) for step in range(SAMPLES + 1)]
        shown = [(point, self._display(point.x, point.y)) for point in points]
        shown = [(point, spot) for point, spot in shown if self._on_card(spot)]
        if not shown:
            return
        taken = [text.get_window_extent(self.render) for text in self.ax.texts] + [axes.get_window_extent(self.render) for axes in self.fig.axes[1:]]
        points_per_pixel = 72 / self.fig.dpi
        for gap in NAME_GAPS_PT:
            point = min(shown, key=lambda entry: abs(np.linalg.norm(entry[1] - anchor) * points_per_pixel - gap))[0]
            for va, dy in (("bottom", 3), ("top", -3)):
                label = self.ax.text(point.x, point.y, name, transform=GEODETIC, fontsize=NAME_SIZE, fontstyle="italic", color=RIVER_TEXT, ha="center", va=va,
                                     zorder=16, path_effects=[effects.withStroke(linewidth=1.6, foreground=LAND_HALO)])
                label.set_position(self._shifted(point, dy))
                box = label.get_window_extent(self.render)
                if self._box_on_card(box) and not any(box.overlaps(other) for other in taken):
                    return
                label.remove()

    def _shifted(self, point: Point, dy_pt: float) -> tuple[float, float]:
        x, y = self.projection.transform_point(point.x, point.y, GEODETIC)
        return GEODETIC.transform_point(x, y + dy_pt * self.unit, self.projection)

    def _on_card(self, spot: np.ndarray) -> bool:
        box = self.ax.get_window_extent(self.render)
        margin = 20 * self.fig.dpi / 72
        return box.x0 + margin < spot[0] < box.x1 - margin and box.y0 + margin < spot[1] < box.y1 - margin

    def _box_on_card(self, box) -> bool:
        card = self.ax.get_window_extent(self.render)
        return card.x0 <= box.x0 and box.x1 <= card.x1 and card.y0 <= box.y0 and box.y1 <= card.y1

    def _water_name(self, labeller: WaterLabeller, water: Waterway) -> None:
        if water.shape is None or water.shape.empty:
            return
        body = water.shape.union_all()
        end = nearest_points(self.feature, body)[0]
        centre = self.feature.centroid
        toward = math.atan2(end.y - centre.y, end.x - centre.x)
        if labeller.place(end, water.name, toward, body) is None and (lens := self._lens()) is not None:
            x0, x1, y0, y1 = lens.get_extent(crs=self.projection)
            extent = Extent(x1 - x0, y1 - y0, (x0 + x1) / 2, (y0 + y1) / 2)
            unit = extent.width / (lens.get_position().width * self.fig.get_size_inches()[0] * 72)
            WaterLabeller(lens, self.projection, extent, unit, labeller.land).place(end, water.name, toward, body)

    def _lens(self):
        return next((axes for axes in self.fig.axes[1:] if getattr(axes, "projection", None) == self.projection), None)


def outside(lines: gpd.GeoSeries, feature) -> gpd.GeoSeries:
    if feature.geom_type not in ("Polygon", "MultiPolygon"):
        return lines
    clipped = lines.difference(feature)
    return clipped[~clipped.is_empty]


def draw_river(ax, lines: gpd.GeoSeries) -> None:
    shapes = list(lines.geometry)
    ax.add_geometries(shapes, crs=GEODETIC, facecolor="none", edgecolor="white", linewidth=RIVER_EDGE, alpha=0.7, zorder=RIVER_ZORDER - 0.01)
    ax.add_geometries(shapes, crs=GEODETIC, facecolor="none", edgecolor=RIVER_GREY, linewidth=RIVER_WIDTH, zorder=RIVER_ZORDER)


def paint_surroundings(surroundings: Surroundings, feature: gpd.GeoSeries, renderer: LocatorMap, fig, projection: ccrs.Projection, extent) -> None:
    SurroundingsPainter(surroundings, feature, renderer, fig, projection, extent).paint()


def paint_surroundings_in_lens(surroundings: Surroundings, feature: gpd.GeoSeries, ax, projection: ccrs.Projection) -> None:
    area = feature.union_all()
    for river in surroundings.rivers:
        draw_river(ax, outside(river.shape, area))
