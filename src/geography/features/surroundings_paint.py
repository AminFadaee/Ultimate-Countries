import math

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.patheffects as effects
import numpy as np
import shapely
from matplotlib import patches
from matplotlib.collections import PathCollection
from matplotlib.transforms import Bbox
from shapely.geometry import Point
from shapely.ops import nearest_points

from geography.features.kinds import Kind
from geography.features.riverart import icons
from geography.features.spatial import CRS
from geography.features.surroundings import Surroundings, Waterway
from geography.features.water_labels import WaterLabeller
from geography.render import Extent, LocatorMap

GEODETIC = ccrs.PlateCarree()
RIVER_BLUE = "#4a8fc9"
RIVER_TEXT = "#2a6496"
LAND_HALO = "#e9dfc7"
RIVER_WIDTH, RIVER_EDGE = 2.0, 3.0
RIVER_ZORDER = 2.6
BORDERING_DEG = 0.5
RIVER_OVER_AREA = 3.5
ICONS_ON_TOP = 8.5
LANDMARK_ICONS = {Kind.WATERFALL: (icons.waterfall, 28, 0.62), Kind.CANYON: (icons.gorge, 24, 0.94),
                  Kind.MOUNTAIN: (icons.ink_mountain, 26, 0.96), Kind.VOLCANO: (icons.ink_mountain, 26, 0.96)}
TEXTURES = {Kind.DESERT: icons.desert, Kind.RAINFOREST: icons.forest, Kind.WETLAND: icons.wet_marsh}
TEXTURE_PT, TEXTURE_ICONS = 20, 3
PEAK_PT = 18
METRES_PER_DEGREE = 111_320
RANGE_NAME_GAP_PT = 60
TEXTURE_SPACING = 1.6
RANGE_FILL, RANGE_EDGE, RANGE_TEXT, RANGE_CHIP = "#cdb892", "#8c7355", "#6b5740", "#f3e8d3"
TOWN_DOT, TOWN_TEXT = "#2b2b2b", "#262626"
ICON_SHARE_OF_MARKER = 0.75
MIN_ICON_PT = 20
POINT_REACH_RINGS = 3
NAME_SIZE = 8
NAME_GAPS_PT = (45, 70, 30, 100, 140)
SAMPLES = 400


class SurroundingsPainter:
    def __init__(self, surroundings: Surroundings, feature: gpd.GeoSeries, renderer: LocatorMap, fig, projection: ccrs.Projection, extent):
        self.surroundings, self.renderer, self.fig, self.projection, self.extent = surroundings, renderer, fig, projection, extent
        self.feature = feature.union_all()
        self.rivers = [(river, river.shape if surroundings.rivers_inside else outside(river.shape, self.feature)) for river in surroundings.rivers]
        self.ax = fig.axes[0]
        self.unit = extent.width / (fig.get_size_inches()[0] * 72)
        self.render = fig.canvas.get_renderer()

    def paint(self) -> None:
        for outline in self.surroundings.ranges:
            self._range(outline)
        if self.surroundings.texture in TEXTURES:
            self._texture()
        for _, lines in self.rivers:
            draw_river(self.ax, lines, RIVER_OVER_AREA if self.surroundings.rivers_inside else RIVER_ZORDER)
        for peak in self.surroundings.peaks:
            self._peak(peak)
        for town in self.surroundings.towns:
            self._town(town)
        if self.surroundings.landmark in LANDMARK_ICONS:
            self._landmark()
        for river, lines in self.rivers:
            self._river_name(river.name, lines)
        if self.surroundings.waters:
            labeller = WaterLabeller(self.ax, self.projection, self.extent, self.unit, self.renderer.data.countries.geometry.union_all())
            for water in self.surroundings.waters:
                self._water_name(labeller, water)

    def _range(self, outline: Waterway) -> None:
        shapes = list(outline.shape.geometry)
        self.ax.add_geometries(shapes, crs=GEODETIC, facecolor=RANGE_FILL, edgecolor="none", alpha=0.35, zorder=2.2)
        self.ax.add_geometries(shapes, crs=GEODETIC, facecolor="none", edgecolor=RANGE_EDGE, linestyle=(0, (4, 3)), linewidth=1.2, zorder=2.3)
        label = self.ax.text(0, 0, outline.name, transform=self.projection, fontsize=9, fontweight="bold", color=RANGE_TEXT, ha="center", va="center",
                             zorder=16, bbox={"boxstyle": "round,pad=0.22,rounding_size=0.6", "facecolor": RANGE_CHIP, "edgecolor": RANGE_EDGE, "linewidth": 1.0})
        area = gpd.GeoSeries([outline.shape.union_all()], crs=CRS).to_crs(self.projection.proj4_init).union_all()
        x0, x1, y0, y1 = self.extent.bounds
        shown = area.intersection(shapely.box(x0, y0, x1, y1))
        if shown.is_empty:
            label.remove()
            return
        sx, sy = self.projection.transform_point(self.feature.representative_point().x, self.feature.representative_point().y, GEODETIC)
        taken = self._taken()
        bx0, by0, bx1, by1 = shown.bounds
        spots = sorted((Point(x, y) for x in np.linspace(bx0, bx1, 16) for y in np.linspace(by0, by1, 16) if shown.contains(Point(x, y))),
                       key=lambda point: abs(math.dist((point.x, point.y), (sx, sy)) / self.unit - RANGE_NAME_GAP_PT))
        for spot in spots:
            label.set_position((spot.x, spot.y))
            box = label.get_window_extent(self.render)
            if self._box_on_card(box) and not any(box.overlaps(other) for other in taken):
                return
        label.remove()

    def _taken(self) -> list:
        boxes = [text.get_window_extent(self.render) for text in self.ax.texts if text.get_visible()]
        boxes += [axes.get_window_extent(self.render) for axes in self.fig.axes[1:]]
        for markers in self.ax.collections:
            if isinstance(markers, PathCollection) and len(markers.get_sizes()):
                radius = np.sqrt(markers.get_sizes().max()) / 2 * self.fig.dpi / 72
                for x, y in markers.get_offset_transform().transform(markers.get_offsets()):
                    boxes.append(Bbox.from_extents(x - radius, y - radius, x + radius, y + radius))
        for circle in self.fig.artists:
            if isinstance(circle, patches.Circle):
                centre, radius = np.array(circle.center) * self.fig.dpi, circle.radius * self.fig.dpi
                boxes.append(Bbox.from_extents(*(centre - radius), *(centre + radius)))
        return boxes

    def _texture(self) -> None:
        draw = TEXTURES[self.surroundings.texture]
        size = TEXTURE_PT * self.unit
        area = gpd.GeoSeries([self.feature], crs=CRS).to_crs(self.projection.proj4_init).union_all()
        inner = area.buffer(-size)
        if inner.is_empty:
            return
        rivers = [lines.to_crs(self.projection.proj4_init).union_all().buffer(size) for _, lines in self.rivers]
        inner = inner.difference(shapely.union_all(rivers)) if rivers else inner
        x0, y0, x1, y1 = inner.bounds
        grid = [Point(x, y) for x in np.linspace(x0, x1, 12) for y in np.linspace(y0, y1, 12) if inner.contains(Point(x, y))]
        chosen = [min(grid, key=lambda point: point.distance(area.centroid))] if grid else []
        while len(chosen) < TEXTURE_ICONS:
            grid = [point for point in grid if min(point.distance(other) for other in chosen) >= TEXTURE_SPACING * size]
            if not grid:
                break
            chosen.append(max(grid, key=lambda point: min(point.distance(other) for other in chosen)))
        for point in chosen:
            draw(self.ax, point.x, point.y - size / 2, size, zorder=ICONS_ON_TOP - 1)

    def _peak(self, peak) -> None:
        x, y = self.projection.transform_point(peak.point.x, peak.point.y, GEODETIC)
        size = PEAK_PT * self.unit
        icons.ink_mountain(self.ax, x, y - 0.3 * size, size, zorder=ICONS_ON_TOP)
        self.ax.text(x, y - 0.4 * size, peak.name, transform=self.projection, fontsize=8, fontweight="bold", color=RANGE_TEXT, ha="center", va="top",
                     zorder=16, path_effects=[effects.withStroke(linewidth=1.6, foreground=LAND_HALO)])

    def _town(self, town) -> None:
        x, y = self.projection.transform_point(town.point.x, town.point.y, GEODETIC)
        self.ax.plot([x], [y], marker="o", markersize=4.5, color=TOWN_DOT, markeredgecolor="white", markeredgewidth=0.9, zorder=ICONS_ON_TOP)
        taken = self._taken()
        gap = 5 * self.unit
        for dx, dy, ha, va in ((gap, 0, "left", "center"), (-gap, 0, "right", "center"), (0, gap, "center", "bottom"), (0, -gap, "center", "top")):
            label = self.ax.text(x + dx, y + dy, town.name, transform=self.projection, fontsize=9, fontweight="bold", color=TOWN_TEXT, ha=ha, va=va,
                                 zorder=16, path_effects=[effects.withStroke(linewidth=2.2, foreground="white")])
            box = label.get_window_extent(self.render)
            if self._box_on_card(box) and not any(box.overlaps(other) for other in taken):
                return
            label.remove()

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
                if base < self.extent.bounds[2]:
                    base = y + radius_pt * self.unit
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
        centre = self.feature.centroid
        if water.shape is not None and not water.shape.empty:
            body, reach = water.shape.union_all(), None
            if self.feature.geom_type in ("Polygon", "MultiPolygon"):
                body = body.difference(self.feature)
            body = self._facing_piece(body)
            if body is None:
                return
            spot = nearest_points(self.feature, body)[0]
        elif water.point is not None and not labeller.land.contains(water.point):
            body, reach, spot = None, POINT_REACH_RINGS, water.point
        else:
            return
        toward = math.atan2(spot.y - centre.y, spot.x - centre.x)
        if labeller.place(spot, water.name, toward, body, reach) is None and (lens := self._lens()) is not None:
            x0, x1, y0, y1 = lens.get_extent(crs=self.projection)
            extent = Extent(x1 - x0, y1 - y0, (x0 + x1) / 2, (y0 + y1) / 2)
            unit = extent.width / (lens.get_position().width * self.fig.get_size_inches()[0] * 72)
            WaterLabeller(lens, self.projection, extent, unit, labeller.land).place(spot, water.name, toward, body, reach)

    def _facing_piece(self, body):
        pieces = list(getattr(body, "geoms", [body]))
        if self.feature.geom_type in ("Polygon", "MultiPolygon"):
            pieces = [piece for piece in pieces if piece.distance(self.feature) <= BORDERING_DEG]
        return min(pieces, key=lambda piece: piece.distance(self.feature), default=None)

    def _lens(self):
        return next((axes for axes in self.fig.axes[1:] if getattr(axes, "projection", None) == self.projection), None)


def outside(lines: gpd.GeoSeries, feature) -> gpd.GeoSeries:
    if feature.geom_type not in ("Polygon", "MultiPolygon"):
        return lines
    clipped = lines.difference(feature)
    return clipped[~clipped.is_empty]


def draw_river(ax, lines: gpd.GeoSeries, zorder: float = RIVER_ZORDER) -> None:
    shapes = list(lines.geometry)
    ax.add_geometries(shapes, crs=GEODETIC, facecolor="none", edgecolor="white", linewidth=RIVER_EDGE, alpha=0.7, zorder=zorder - 0.01)
    ax.add_geometries(shapes, crs=GEODETIC, facecolor="none", edgecolor=RIVER_BLUE, linewidth=RIVER_WIDTH, zorder=zorder)


def paint_surroundings(surroundings: Surroundings, feature: gpd.GeoSeries, renderer: LocatorMap, fig, projection: ccrs.Projection, extent) -> None:
    SurroundingsPainter(surroundings, feature, renderer, fig, projection, extent).paint()


def paint_surroundings_in_lens(surroundings: Surroundings, feature: gpd.GeoSeries, ax, projection: ccrs.Projection) -> None:
    area = feature.union_all()
    for river in surroundings.rivers:
        draw_river(ax, river.shape if surroundings.rivers_inside else outside(river.shape, area), RIVER_OVER_AREA if surroundings.rivers_inside else RIVER_ZORDER)
