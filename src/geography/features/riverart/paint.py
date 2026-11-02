import math

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.colors
import matplotlib.patches
import matplotlib.patheffects as effects
import numpy as np
from matplotlib.collections import PolyCollection
from matplotlib.transforms import Bbox
from shapely.geometry import Point, box
from shapely.ops import polylabel

from geography.country_labels import Placer
from geography.features.riverart import icons
from geography.features.riverart.facts import RiverArt
from geography.features.riverart.kinds import MouthKind, SourceKind
from geography.features.spatial import CRS
from geography.render import LocatorMap, get_visible

GEODETIC = ccrs.PlateCarree()
METRES_PER_DEGREE = 111_320
RIVER_BLUE, TRIBUTARY_BLUE, LINK_GREY = "#4f93cf", "#a9c9e2", "#8a9099"
WATER_TEXT, SEA_TEXT, LAND_TEXT = "#2f6a99", "#2a6496", "#5b4a35"
LAND_HALO = "#e9dfc7"
FEATURE_TEXT = "#1d4f7c"
CHIP = {"boxstyle": "round,pad=0.2,rounding_size=0.6", "facecolor": "#dcecf8", "edgecolor": "#4f93cf", "linewidth": 0.8, "alpha": 0.96}
CHIP_STYLES = {
    SourceKind.GLACIER: ("#3f6280", "#eaf4fa", "#6fa3c4"),
    SourceKind.HIGHLAND: ("#8a6a43", "#f3e8d3", "#8a6a43"),
    SourceKind.WETLAND: ("#3f5f30", "#e8f0d8", "#6f9a4f"),
}
PLACE_CHIP = ("#5d574b", "#efe9dc", "#8a8478")
DEFAULT_CHIP = ("#245b88", CHIP["facecolor"], CHIP["edgecolor"])
SAMPLES = 500
RIBBON_HALF_WIDTH = 2.3
CHEVRON_ARM, CHEVRON_ANGLE = 4.7, math.radians(42)
CHEVRON_SPACING = 85
MAX_CHEVRONS, MIN_CHEVRONS = 8, 4
MAX_PEAKS = 48
LAKE_ICON_PT = (16, 20)
WIDE_LAKE_PT = 70
COUNTRY_LABELS = 8
ICONS_ON_TOP = 8.5
PLACE_ICONS = {
    SourceKind.CITY: icons.place_city, SourceKind.COMMUNE: icons.place_commune, SourceKind.TOWN: icons.place_town, SourceKind.VILLAGE: icons.place_village,
}


def halo(width: float = 1.6, colour: str = LAND_HALO) -> list:
    return [effects.withStroke(linewidth=width, foreground=colour)]


def two_lines(text: str) -> str | None:
    words = text.split()
    if len(words) < 2:
        return None
    cut = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
    return " ".join(words[:cut]) + "\n" + " ".join(words[cut:])


def aligned_to(angle: float) -> dict:
    return {
        "ha": "left" if math.cos(angle) > 0.35 else "right" if math.cos(angle) < -0.35 else "center",
        "va": "bottom" if math.sin(angle) > 0.35 else "top" if math.sin(angle) < -0.35 else "center_baseline",
    }


def unit_vector(vector: np.ndarray) -> np.ndarray:
    return vector / (np.linalg.norm(vector) or 1)


class RiverPainter:
    def __init__(self, art: RiverArt, renderer: LocatorMap, fig, projection: ccrs.Projection, extent):
        self.art, self.renderer, self.fig, self.projection, self.extent = art, renderer, fig, projection, extent
        self.ax = fig.axes[0]
        self.unit = extent.width / (fig.get_size_inches()[0] * 72)
        self.kind = art.source.kind if art.source else SourceKind.UNKNOWN
        self.source_point = art.source_point
        self._trace_path()

    def paint(self) -> None:
        self._areas()
        self._linked_and_tributaries()
        if self.kind in (SourceKind.HIGHLAND, SourceKind.GLACIER):
            self._peaks()
        self._path_lakes()
        self._landmarks(self.art.dams, icons.dam, 24, 6, 21)
        self._landmarks(self.art.gorges, icons.gorge, 24, 6, 21)
        self._falls()
        self._ribbon()
        self._source_icon()
        self._chevrons()
        self._source_label()
        self._mouth_label()
        self._separate_labels()
        self._country_labels()

    def deg(self, points: float) -> float:
        return points * self.unit / METRES_PER_DEGREE

    def project(self, x: float, y: float) -> tuple[float, float]:
        return self.projection.transform_point(x, y, GEODETIC)

    def geodetic(self, x: float, y: float) -> tuple[float, float]:
        return GEODETIC.transform_point(x, y, self.projection)

    def text(self, x: float, y: float, text: str, size: float = 8, **style) -> matplotlib.text.Text:
        return self.ax.text(x, y, text, transform=GEODETIC, fontsize=size, zorder=16, **{"ha": "center", "va": "center_baseline", **style})

    def plain(self, x: float, y: float, text: str, colour: str = FEATURE_TEXT, size: float = 8, **style):
        return self.text(x, y, text, size, color=colour, path_effects=halo(), **style)

    def chip(self, x: float, y: float, text: str, kind: SourceKind | None = None, size: float = 9):
        colour, face, edge = PLACE_CHIP if kind and kind.is_place else CHIP_STYLES.get(kind, DEFAULT_CHIP)
        style = {**CHIP, "facecolor": face, "edgecolor": edge}
        if kind in (SourceKind.HIGHLAND, SourceKind.GLACIER):
            style.update(boxstyle="round,pad=0.22,rounding_size=0.6", linewidth=1.0)
        return self.text(x, y, text, size, fontweight="bold", color=colour, bbox=style)

    def anchor(self) -> Point:
        near = self.art.upstream.distance(self.source_point) * METRES_PER_DEGREE * max(math.cos(math.radians(self.source_point.y)), 0.2) / self.unit <= 36
        return self.source_point if near else self.art.upstream

    def _trace_path(self) -> None:
        line = self.art.path
        points = [line.interpolate(i / SAMPLES, normalized=True) for i in range(SAMPLES + 1)]
        self.xy = self.projection.transform_points(GEODETIC, np.array([p.x for p in points]), np.array([p.y for p in points]))[:, :2]
        tangent = np.gradient(self.xy, axis=0)
        self.tangent = tangent / (np.linalg.norm(tangent, axis=1, keepdims=True) + 1e-12)
        self.normal = np.stack([-self.tangent[:, 1], self.tangent[:, 0]], axis=1)
        self.along = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(self.xy, axis=0), axis=1))]) / self.unit

    def _outline(self, shape: gpd.GeoSeries, face: str, edge: str, alpha: float, dashed: str | None = None) -> None:
        self.ax.add_geometries(list(shape.geometry), crs=GEODETIC, facecolor=face, edgecolor=edge, alpha=alpha, linewidth=1.2, zorder=2)
        self.ax.add_geometries(list(shape.geometry), crs=GEODETIC, facecolor="none", edgecolor=dashed or edge, linestyle=(0, (4, 3)), linewidth=1.2, zorder=3)

    def _areas(self) -> None:
        if self.art.source_area is not None:
            self._outline(self.art.source_area, "#cdb892", "#8c7355", 0.35, "#6b5740")
        sink = self.art.sink
        if sink and sink.outline is not None and self.art.mouth.kind is not MouthKind.LAKE:
            face, edge = {MouthKind.DRIED: ("#d0d0d0", "#6f6f6f"), MouthKind.DELTA: ("#cfd9a8", "#6f7a3f")}.get(self.art.mouth.kind, ("#cdb892", "#6b5740"))
            self._outline(sink.outline, face, edge, 0.55)

    def _linked_and_tributaries(self) -> None:
        for lines in self.art.linked.values():
            self.ax.add_geometries(list(lines.geometry), crs=GEODETIC, facecolor="none", edgecolor="white", linewidth=3.4, alpha=0.7, zorder=4.7)
            self.ax.add_geometries(list(lines.geometry), crs=GEODETIC, facecolor="none", edgecolor=LINK_GREY, linewidth=2.4, zorder=4.8)
        for name in self.art.source_rivers:
            ends = [Point(c) for line in self.art.linked[name].explode(index_parts=False) for c in (line.coords[0], line.coords[-1])]
            far = max(ends, key=lambda p: p.distance(self.art.upstream))
            self.text(far.x, far.y, name, 8, ha="center", va="bottom", fontstyle="italic", color=WATER_TEXT, path_effects=halo())
        for lines in self.art.tributaries.values():
            self.ax.add_geometries(list(lines.geometry), crs=GEODETIC, facecolor="none", edgecolor="white", linewidth=1.8, alpha=0.45, zorder=3.5)
            self.ax.add_geometries(list(lines.geometry), crs=GEODETIC, facecolor="none", edgecolor=TRIBUTARY_BLUE, linewidth=0.9, zorder=4)

    def _width_pts(self, shape: gpd.GeoSeries) -> float:
        x0, y0, x1, y1 = shape.total_bounds
        return (x1 - x0) * METRES_PER_DEGREE * max(math.cos(math.radians((y0 + y1) / 2)), 0.2) / self.unit

    def _peak_spots(self, area, size: float) -> list[tuple[float, float, float]]:
        x0, y0, x1, y1 = area.bounds
        wide = (x1 - x0) >= 8
        random = np.random.default_rng(int(abs(area.centroid.x * 1000)) + int(abs(area.centroid.y * 1000)))
        while True:
            step = self.deg(size) * (1.15 if wide else 0.62)
            inner = area.buffer(-self.deg(size) * 0.45)
            spots, row, y = [], 0, y1 - step * 0.3
            while y > y0 and len(spots) <= MAX_PEAKS:
                x = x0 + (step * 0.5 if row % 2 else 0)
                while x < x1 and len(spots) <= MAX_PEAKS:
                    if inner.contains(Point(x, y)):
                        spots.append((x + random.uniform(-0.15, 0.15) * step, y + random.uniform(-0.08, 0.08) * step, size * random.uniform(0.72, 1.35)))
                    x += step
                y -= step * (0.40 if wide else 0.55)
                row += 1
            if len(spots) <= MAX_PEAKS:
                return spots
            size *= 1.2

    def _peaks(self) -> None:
        glacier = self.kind is SourceKind.GLACIER
        area = self.art.source_area.union_all() if self.art.source_area is not None else None
        size = 34 if glacier else 24 if area is None or (area.bounds[2] - area.bounds[0]) < 8 else 30
        if area is not None:
            size = max(size, self._width_pts(self.art.source_area) / 7)
        centre = area.representative_point() if area is not None else self.anchor()
        if glacier:
            unit = self.deg(size)
            spots = [(centre.x, centre.y + unit * 0.45, size), (centre.x - unit * 0.62, centre.y + unit * 0.55, size * 0.62), (centre.x + unit * 0.64, centre.y + unit * 0.55, size * 0.62)]
        elif area is None or (area.bounds[2] - area.bounds[0]) < self.deg(size) * 6:
            unit = self.deg(size)
            spots = [
                (centre.x - unit * 0.55, centre.y - unit * 0.05, size - 4), (centre.x + unit * 0.62, centre.y - unit * 0.05, size - 4), (centre.x - unit * 0.05, centre.y + unit * 0.38, size + 6),
                (centre.x - unit * 1.05, centre.y - unit * 0.22, size - 8), (centre.x + unit * 1.12, centre.y - unit * 0.22, size - 8),
            ]
        else:
            spots = self._peak_spots(area, size)
        for index, (x, y, points) in enumerate(sorted(spots, key=lambda spot: (-spot[1], spot[0]))):
            px, py = self.project(x, y)
            z = 5 + index * 0.004
            if glacier:
                icons.ink_glacier(self.ax, px, py - points * self.unit * 0.4, points * self.unit, tongue=points == size, zorder=z)
            else:
                icons.ink_mountain(self.ax, px, py - points * self.unit * 0.4, points * self.unit, 1.0, z)

    def _landmarks(self, items, draw, size: float, raise_by: float, drop: float) -> None:
        for item in items:
            snapped = self.art.path.interpolate(self.art.path.project(Point(item.point)))
            px, py = self.project(snapped.x, snapped.y)
            draw(self.ax, px, py - raise_by * self.unit, size * self.unit, zorder=ICONS_ON_TOP)
            self.plain(snapped.x, snapped.y - self.deg(drop), item.name)

    def _falls(self) -> None:
        for fall in self.art.falls:
            px, py = self.project(*fall.point)
            icons.waterfall(self.ax, px, py - 19 * self.unit, 28 * self.unit)
            self.plain(fall.point[0], fall.point[1] - self.deg(31), fall.name)

    def _long_pts(self, shape: gpd.GeoSeries) -> float:
        whole = shape.union_all()
        ring = list(whole.minimum_rotated_rectangle.exterior.coords)[:4]
        return max(math.dist(ring[i], ring[(i + 1) % 4]) for i in range(4)) * METRES_PER_DEGREE * max(math.cos(math.radians(whole.representative_point().y)), 0.2) / self.unit

    def _path_lakes(self) -> None:
        for lake in self.art.lakes:
            self._lake(lake.landmark.name, lake.outline, Point(lake.landmark.point))

    def _lake(self, name: str, outline: gpd.GeoSeries | None, point: Point, source: bool = False, away: np.ndarray | None = None) -> None:
        if outline is not None and self._long_pts(outline) >= WIDE_LAKE_PT:
            self._water_label(name, outline, self.art.upstream if source else point, away)
            return
        centre = outline.union_all().representative_point() if outline is not None else point
        if source:
            size = float(np.clip(self._long_pts(outline), *LAKE_ICON_PT)) if outline is not None else 18.0
        else:
            size = float(np.clip(self._long_pts(outline) * 0.5, 12, 16)) if outline is not None else 14.0
        px, py = self.project(centre.x, centre.y)
        if source:
            back = unit_vector(self.xy[0] - self.xy[min(12, len(self.xy) - 1)])
            px, py = self.xy[0] + back * 0.8 * size * self.unit * 0.75
            centre = Point(self.geodetic(px, py))
        icons.ink_lake(self.ax, px, py, size * self.unit, 1.5, 0.0, zorder=ICONS_ON_TOP)
        drop = self.deg(size * 0.5 + 11)
        if source:
            self.chip(centre.x, centre.y - drop, name, None, 8)
        else:
            self.plain(centre.x, centre.y - drop, name)

    def _water_label(self, name: str, outline: gpd.GeoSeries, near: Point, away: np.ndarray | None = None) -> None:
        layouts = [name] + ([wrapped] if (wrapped := two_lines(name)) else [])
        half_h = 0.2 * self.extent.height / METRES_PER_DEGREE
        half_w = 0.2 * self.extent.width / (METRES_PER_DEGREE * max(math.cos(math.radians(near.y)), 0.2))
        local = outline.union_all().intersection(box(near.x - half_w, near.y - half_h, near.x + half_w, near.y + half_h))
        shape = local if not local.is_empty else outline.union_all()
        parts = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
        biggest = max(parts, key=lambda part: part.area)
        for size in (9.5, 8.5, 7.5, 6.5):
            for text in layouts:
                lines = text.split("\n")
                for spot in (biggest.representative_point(), polylabel(biggest, tolerance=0.01)):
                    width = 0.62 * size * max(len(line) for line in lines) * 1.1 * self.unit / (METRES_PER_DEGREE * max(math.cos(math.radians(spot.y)), 0.2)) / 2
                    height = size * 1.5 * len(lines) * self.unit / METRES_PER_DEGREE / 2
                    if biggest.contains(box(spot.x - width, spot.y - height, spot.x + width, spot.y + height)):
                        self.text(spot.x, spot.y, text, size, va="center", linespacing=1.05, color=SEA_TEXT, path_effects=halo(1.2, "#aedcf5"))
                        return
        self.plain(biggest.centroid.x, biggest.bounds[1] - self.deg(8), name, SEA_TEXT)

    def _ribbon(self) -> None:
        sink = self.art.sink
        fade_from = 0.80 if sink and sink.fades and self.art.mouth.kind is MouthKind.LAND else 1.0
        taper = np.clip((np.linspace(0, 1, SAMPLES + 1) - fade_from) / (1 - fade_from + 1e-9), 0, 1)
        half = (RIBBON_HALF_WIDTH * (1 - 0.8 * taper))[:, None] * self.unit
        left, right = self.xy + self.normal * half, self.xy - self.normal * half
        base = matplotlib.colors.to_rgb(RIVER_BLUE)
        colours = [(*base, 1 - 0.85 * taper[i]) for i in range(SAMPLES)]
        self.ax.add_collection(PolyCollection([[left[i], left[i + 1], right[i + 1], right[i]] for i in range(SAMPLES)], facecolors=colours, edgecolors=colours, linewidths=0.4, zorder=7))
        self.ax.add_geometries(list(self.art.river.geometry), crs=GEODETIC, facecolor="none", edgecolor=RIVER_BLUE, linewidth=1.1, zorder=6)

    def _icon_size(self) -> float:
        scale, low, high = (0.26, 28, 44) if self.kind.is_place else (0.22, 20, 32)
        return float(np.clip(self.along[-1] * scale, low, high))

    def _source_icon(self) -> None:
        kind = self.kind
        if not kind.has_icon:
            return
        anchor = self.anchor()
        px, py = self.project(anchor.x, anchor.y)
        if kind is SourceKind.SPRING:
            size = float(np.clip(self.along[-1] * 0.2, 14, 26))
            outlet_x, outlet_y = icons.SPRING_OUTLET
            icons.spring(self.ax, px - (outlet_x - 0.5) * size * self.unit, py - outlet_y * size * self.unit, size * self.unit, zorder=ICONS_ON_TOP)
            return
        size = self._icon_size()
        back = unit_vector(self.xy[0] - self.xy[min(12, len(self.xy) - 1)])
        shift = 0.5 if kind is SourceKind.WETLAND else 0.2
        self.icon_box = (px + back[0] * shift * size * self.unit, py + back[1] * shift * size * self.unit - 0.08 * size * self.unit, size)
        swamp = kind is SourceKind.WETLAND and "swamp" in self.art.source.classes
        draw = {SourceKind.WETLAND: icons.wet_swamp if swamp else icons.wet_marsh, SourceKind.CAVE: icons.cave, SourceKind.DAM: icons.dam}.get(kind) or PLACE_ICONS[kind]
        draw(self.ax, self.icon_box[0], self.icon_box[1], size * self.unit, zorder=ICONS_ON_TOP)

    def _chevrons(self) -> None:
        count = int(min(MAX_CHEVRONS, max(MIN_CHEVRONS, round(self.along[-1] / CHEVRON_SPACING))))
        reach, window = int(SAMPLES * 0.025), 30
        low, high = int(SAMPLES * 0.08), int(SAMPLES * 0.92)
        if self.kind.has_icon:
            low = max(low, int(np.searchsorted(self.along, 1.2 * float(np.clip(self.along[-1] * 0.2, 14, 26)))))
        sigma = max(4, SAMPLES // 45)
        kernel = np.exp(-0.5 * (np.arange(-3 * sigma, 3 * sigma + 1) / sigma) ** 2)
        kernel /= kernel.sum()
        padded = np.pad(self.xy, ((3 * sigma, 3 * sigma), (0, 0)), mode="edge")
        smooth = np.stack([np.convolve(padded[:, axis], kernel, mode="valid") for axis in (0, 1)], axis=1)
        tangent = np.gradient(smooth, axis=0)
        tangent /= np.linalg.norm(tangent, axis=1, keepdims=True) + 1e-12
        normal = np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)
        heading = np.unwrap(np.arctan2(tangent[:, 1], tangent[:, 0]))
        for slot in range(count):
            centre = int(low + (high - low) * (slot + 0.5) / count)
            candidates = range(max(window, centre - reach), min(SAMPLES - window, centre + reach) + 1)
            i = min(candidates, key=lambda k: np.ptp(heading[k - window:k + window + 1]))
            tip = self.xy[i] + tangent[i] * CHEVRON_ARM * 0.5 * self.unit
            for sign in (1, -1):
                end = tip - (tangent[i] * math.cos(CHEVRON_ANGLE) - sign * normal[i] * math.sin(CHEVRON_ANGLE)) * CHEVRON_ARM * self.unit
                self.ax.plot([tip[0], end[0]], [tip[1], end[1]], color="#1d4f7c", linewidth=2.5, solid_capstyle="round", zorder=8)
                self.ax.plot([tip[0], end[0]], [tip[1], end[1]], color="#cfe4f5", linewidth=1.6, solid_capstyle="round", zorder=8.1)

    def _flow_away(self) -> np.ndarray:
        head, ahead = self.art.path.interpolate(0, normalized=True), self.art.path.interpolate(0.04, normalized=True)
        return unit_vector(np.array([head.x - ahead.x, head.y - ahead.y]))

    def _source_label(self) -> None:
        source, kind = self.art.source, self.kind
        if source is None:
            return
        away = self._flow_away()
        if kind in (SourceKind.HIGHLAND, SourceKind.GLACIER):
            area = self.art.source_area.union_all() if self.art.source_area is not None else None
            anchor = area.representative_point() if area is not None else self.anchor()
            low = (area.bounds[1] if area is not None else anchor.y) - self.deg(34 if kind is SourceKind.GLACIER else 24)
            self.chip(anchor.x, low, source.name, kind)
        elif kind.has_icon:
            self._icon_chip(source.name, away)
        elif kind is SourceKind.LAKE:
            self._lake(source.name, self.art.source_lake, self.anchor(), True, away)

    def _icon_chip(self, name: str, away: np.ndarray) -> None:
        anchor = self.anchor()
        if self.kind is SourceKind.SPRING:
            reach = 30.0
            position = (anchor.x + away[0] * self.deg(reach), anchor.y + away[1] * self.deg(reach))
        else:
            x, y, size = self.icon_box
            reach = float(np.clip(self.along[-1] * 0.22, 20, 32)) * 1.25 + 10
            position = self.geodetic(x, y + ((1.02 * size + 9) if away[1] >= 0 else -9) * self.unit)
        chip = self.chip(position[0], position[1], name, self.kind)
        renderer = self.fig.canvas.get_renderer()
        if any(chip.get_window_extent(renderer).expanded(1.15, 1.5).overlaps(other.get_window_extent(renderer)) for other in self.fig.axes[1:]):
            chip.set_position((anchor.x - away[0] * self.deg(reach), anchor.y - away[1] * self.deg(reach)))

    def _mouth_label(self) -> None:
        mouth = self.art.mouth
        if mouth is None:
            return
        tail = self.art.path.interpolate(0.97, normalized=True)
        direction = unit_vector(np.array([self.art.mouth_point.x - tail.x, self.art.mouth_point.y - tail.y]))
        if mouth.kind is MouthKind.RIVER:
            horizontal = abs(direction[0]) > abs(direction[1])
            self.text(
                self.art.mouth_point.x + direction[0] * self.deg(10), self.art.mouth_point.y + direction[1] * self.deg(10), mouth.name, 8,
                ha=("left" if direction[0] > 0 else "right") if horizontal else "center",
                va="center_baseline" if horizontal else ("bottom" if direction[1] > 0 else "top"),
                fontstyle="italic", color=WATER_TEXT, path_effects=halo(),
            )
        elif mouth.kind is MouthKind.LAKE:
            self._lake_mouth(mouth.name, direction)
        elif mouth.kind is MouthKind.SEA:
            self._sea_label(mouth.name)
        else:
            self._sink_label(mouth.name, direction)

    def _lake_mouth(self, name: str, direction: np.ndarray) -> None:
        whole = self.art.sink.whole
        if whole is not None and self._long_pts(whole) >= WIDE_LAKE_PT:
            self._water_label(name, whole, self.art.mouth_point, direction)
            return
        flow = unit_vector(self.xy[-1] - self.xy[-min(15, len(self.xy))])
        size = 18.0
        centre = self.xy[-1] + flow * 0.8 * size * 0.5 * 1.5 * self.unit
        icons.ink_lake(self.ax, centre[0], centre[1], size * self.unit, 1.5, 0.0, zorder=ICONS_ON_TOP)
        below = centre - np.array([0.0, (size * 0.5 + 10) * self.unit])
        x, y = self.geodetic(below[0], below[1])
        self.text(x, y, name, 8.5, fontstyle="italic", color=WATER_TEXT, path_effects=halo())

    def _sink_label(self, name: str, direction: np.ndarray) -> None:
        sink, kind = self.art.sink, self.art.mouth.kind
        flow = self.xy[-1] - self.xy[-min(15, len(self.xy))]
        angle = math.atan2(flow[1], flow[0])
        along = np.array([math.cos(angle), math.sin(angle)])
        align = {"ha": "center", "va": "center_baseline"}
        if sink.outline is not None:
            spot = sink.outline.union_all().representative_point()
        elif kind is MouthKind.DELTA:
            size = float(np.clip(self.along[-1] * 0.14, 26, 46))
            icons.delta_fan(self.ax, self.xy[-1][0], self.xy[-1][1], size * self.unit, angle)
            beyond = self.xy[-1] + along * (size + 5) * self.unit
            spot, align = Point(self.geodetic(*beyond)), aligned_to(angle)
        elif sink.fades and kind is MouthKind.LAND:
            self._dunes(along)
            beyond = self.xy[-1] + along * 56 * self.unit
            spot, align = Point(self.geodetic(*beyond)), aligned_to(angle)
        else:
            spot = Point(self.art.mouth_point.x + direction[0] * self.deg(22), self.art.mouth_point.y + direction[1] * self.deg(22))
        suffix = " (dried)" if kind is MouthKind.DRIED else ""
        self.text(spot.x, spot.y, name + suffix, 8.5, **align, fontstyle="italic", color=LAND_TEXT, path_effects=halo())

    def _dunes(self, along: np.ndarray) -> None:
        across = np.array([-along[1], along[0]])
        for ahead, aside, width in ((12, 2, 6), (22, 11, 7), (22, -12, 5), (34, 0, 8), (32, -16, 6), (42, 13, 6)):
            centre = self.xy[-1] + (along * ahead + across * aside) * self.unit
            crest = [(centre[0] + width * self.unit * math.cos(a), centre[1] + 0.45 * width * self.unit * math.sin(a)) for a in np.linspace(0, math.pi, 14)]
            self.ax.add_patch(matplotlib.patches.Polygon(crest, closed=True, facecolor="#dcc28f", edgecolor="#9c8260", linewidth=0.8, zorder=3))

    def _sea_label(self, name: str) -> None:
        from shapely.prepared import prep

        land_all = self.renderer.data.countries.geometry.union_all()
        land = prep(land_all)
        mouth = self.art.mouth_point
        tail = self.art.path.interpolate(0.985, normalized=True)
        flow = math.atan2(mouth.y - tail.y, mouth.x - tail.x)
        wrapped = two_lines(name) or name

        def place(text: str, size: float, rings: int, tolerance: float = 0.0):
            lines = text.split("\n")
            width, height = 0.62 * size * max(len(line) for line in lines), size * 1.5 * len(lines)
            for ring in range(1, rings + 1):
                for turn in sorted(range(24), key=lambda k: abs(math.atan2(math.sin(k * math.pi / 12 - flow), math.cos(k * math.pi / 12 - flow)))):
                    angle = turn * math.pi / 12
                    x, y = mouth.x + self.deg(10) * ring * math.cos(angle), mouth.y + self.deg(10) * ring * math.sin(angle)
                    half_w = width * self.unit / (METRES_PER_DEGREE * max(math.cos(math.radians(y)), 0.2)) / 2 * 1.15
                    cell = box(x - half_w, y - height * self.unit / METRES_PER_DEGREE / 2, x + half_w, y + height * self.unit / METRES_PER_DEGREE / 2)
                    if not land.intersects(cell) or (tolerance and land_all.intersection(cell).area < tolerance * cell.area):
                        return x, y
            return None

        attempts = ((name, 9.5, 14), (wrapped, 9.5, 14), (wrapped, 8, 14), (wrapped, 6.5, 14), (wrapped, 5.5, 14), (name, 5.5, 14), (wrapped, 5.5, 40), (name, 5.5, 40))
        for text, size, rings in attempts:
            if hit := place(text, size, rings, 0.04 if size <= 6.5 and rings == 14 else 0.0):
                self.text(hit[0], hit[1], text, size, va="center", linespacing=1.05, color=SEA_TEXT, path_effects=halo(1.2, "#aedcf5"))
                return

    def _move(self, label, dy: float, dx: float = 0.0) -> None:
        x, y = label.get_position()
        px, py = self.ax.transData.transform(self.project(x, y))
        label.set_position(self.geodetic(*self.ax.transData.inverted().transform((px + dx, py + dy))))

    def _separate_labels(self) -> None:
        self.fig.canvas.draw()
        renderer = self.fig.canvas.get_renderer()
        drawn = list(self.ax.texts)
        for i in range(1, len(drawn)):
            for j in range(i):
                a, b = drawn[i].get_window_extent(renderer), drawn[j].get_window_extent(renderer)
                if a.overlaps(b):
                    self._move(drawn[i], (b.y0 - a.y1 - 2) if a.y0 + a.y1 >= b.y0 + b.y1 else (b.y1 - a.y0 + 2))
        for label in self.ax.texts:
            extent = label.get_window_extent(renderer)
            for other in self.fig.axes[1:]:
                keep = other.get_window_extent(renderer)
                if extent.overlaps(keep):
                    right, up = keep.x1 + 8 - extent.x0, keep.y1 + 8 - extent.y0
                    self._move(label, 0, right) if right <= up else self._move(label, up)
                    extent = label.get_window_extent(renderer)

    def _country_labels(self) -> None:
        renderer = self.fig.canvas.get_renderer()
        pad = self.fig.dpi / 72 * 3.5
        boxes = [t.get_window_extent(renderer) for t in self.ax.texts] + [a.get_window_extent(renderer) for a in self.fig.axes[1:]]
        boxes += [p.get_window_extent(renderer) for p in self.ax.patches if p.get_zorder() >= 4.5]
        samples = [self.xy[::4]] + [
            np.array([self.project(x, y) for g in lines.geometry for part in getattr(g, "geoms", [g]) for x, y in list(part.coords)[::6]]) for lines in self.art.linked.values()
        ]
        for points in samples:
            if len(points):
                boxes += [Bbox.from_extents(x - pad, y - pad, x + pad, y + pad) for x, y in self.ax.transData.transform(points)]
        placer = Placer(self.fig, self.ax, boxes)
        crossed, neighbours = self._countries()
        river = gpd.GeoSeries([self.art.path], crs=CRS).to_crs(self.projection.proj4_init).union_all()
        x0, x1, y0, y1 = self.extent.bounds
        frame = box(x0, y0, x1, y1)
        clear = river.buffer(self.extent.width * 0.014)
        for lake in [self.art.source_lake] + [item.outline for item in self.art.lakes]:
            if lake is not None:
                clear = clear.union(lake.to_crs(self.projection.proj4_init).union_all().buffer(self.extent.width * 0.004))
        for group, is_crossed in ((crossed, True), (neighbours, False)):
            for code, name, shape in zip(group["ADM0_A3"], group["NAME_EN"], group.geometry):
                try:
                    visible = shape.buffer(0).intersection(frame)
                    area = visible.difference(clear) if is_crossed else visible
                except Exception:
                    continue
                if area.is_empty or area.area < 0.0008 * frame.area:
                    continue
                label = self.renderer.country_names.get(code, name)
                placed = placer.place(label, area, None, (9, 8, 7, 6, 5.5), 0.97, 1.0)
                if not placed and is_crossed:
                    tight = visible.difference(river.buffer(self.extent.width * 0.005))
                    if not tight.is_empty:
                        placer.place(label, tight, None, (6.5, 5.5, 5), 0.97, 1.0)

    def _countries(self):
        shown = get_visible(self.renderer.data.countries, self.projection, self.extent).to_crs(self.projection.proj4_init)
        river = gpd.GeoSeries([self.art.path], crs=CRS).to_crs(self.projection.proj4_init).union_all()
        crossed = shown[shown.geometry.intersects(river.buffer(self.extent.width * 0.004))]
        crossed = crossed.assign(run=crossed.geometry.intersection(river).length).sort_values("run", ascending=False).head(COUNTRY_LABELS)
        ring = crossed.geometry.union_all().buffer(self.extent.width * 0.012) if len(crossed) else river
        around = shown[~shown.index.isin(crossed.index) & shown.geometry.intersects(ring)]
        around = around.assign(seen=around.geometry.area).sort_values("seen", ascending=False).head(max(0, COUNTRY_LABELS - len(crossed)))
        return crossed, around


def paint_river(art: RiverArt, renderer: LocatorMap, fig, projection: ccrs.Projection, extent) -> None:
    RiverPainter(art, renderer, fig, projection, extent).paint()
