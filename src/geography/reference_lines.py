import functools
from dataclasses import dataclass

import cartopy.crs as ccrs
import numpy as np
from matplotlib.transforms import offset_copy
from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geography.sources.natural_earth_physical import PhysicalLayer, load_layer

LABELS = {"Equator": "Equator", "International Date Line": "Date line"}
LINE_COLOR = "#4a4a4a"
LINE_WIDTH = 1.1
DASHES = (0, (6, 4))
LABEL_SIZE = 12
LABEL_GAP = 3
LABEL_STEPS = 200
GEODETIC = ccrs.PlateCarree()


@dataclass(frozen=True)
class Reference:
    name: str
    geometry: BaseGeometry


@functools.cache
def references() -> tuple[Reference, ...]:
    lines = load_layer(PhysicalLayer.LINES)
    return tuple(Reference(label, lines[lines["name"] == name].geometry.union_all()) for name, label in LABELS.items())


def draw_references(ax, projection: ccrs.Projection, frame: tuple[float, float, float, float], margin: float, blocked: BaseGeometry, focus: Point) -> None:
    xmin, ymin, xmax, ymax = frame
    inner = box(xmin + margin, ymin + margin, xmax - margin, ymax - margin)
    for reference in references():
        line = projection.project_geometry(reference.geometry, GEODETIC).intersection(box(*frame))
        label = place_label(ax, reference.name, line.intersection(inner), inner, blocked, focus)
        if label is None:
            continue
        blocked = blocked.union(label)
        ax.add_geometries([line], crs=projection, facecolor="none", edgecolor=LINE_COLOR, linewidth=LINE_WIDTH, linestyle=DASHES, zorder=6)


def spots_along(line: BaseGeometry) -> list[tuple[Point, Point]]:
    spots = []
    for run in getattr(line, "geoms", [line]):
        if run.length == 0:
            continue
        step = run.length / LABEL_STEPS
        spots += [(run.interpolate(distance), run.interpolate(min(distance + step, run.length))) for distance in np.linspace(0, run.length, LABEL_STEPS)]
    return spots


def reading_angle(start: Point, ahead: Point) -> float:
    angle = np.degrees(np.arctan2(ahead.y - start.y, ahead.x - start.x))
    return (angle + 80) % 180 - 80


def place_label(ax, name: str, line: BaseGeometry, inner, blocked: BaseGeometry, focus: Point) -> BaseGeometry | None:
    if line.is_empty:
        return None
    renderer = ax.figure.canvas.get_renderer()
    for start, ahead in sorted(spots_along(line), key=lambda spot: spot[0].distance(focus)):
        angle = reading_angle(start, ahead)
        normal = np.radians(angle + 90)
        transform = offset_copy(ax.transData, fig=ax.figure, x=LABEL_GAP * np.cos(normal), y=LABEL_GAP * np.sin(normal), units="points")
        text = ax.text(
            start.x, start.y, name, rotation=angle, rotation_mode="anchor", ha="center", va="bottom",
            transform=transform, fontsize=LABEL_SIZE, color=LINE_COLOR, style="italic", zorder=7,
        )
        corners = ax.transData.inverted().transform(text.get_window_extent(renderer).corners())
        area = box(*corners.min(axis=0), *corners.max(axis=0))
        if inner.contains(area) and not area.intersects(blocked):
            return area
        text.remove()
    return None


def blocked_area(land: list[BaseGeometry], projection: ccrs.Projection, keep_clear: list[BaseGeometry]) -> BaseGeometry:
    return unary_union([projection.project_geometry(area, GEODETIC) for area in land] + keep_clear)
