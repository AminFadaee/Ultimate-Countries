import math

import cartopy.crs as ccrs
import matplotlib.patheffects as effects
from shapely.geometry import Point, box
from shapely.prepared import prep

GEODETIC = ccrs.PlateCarree()
METRES_PER_DEGREE = 111_320
SEA_TEXT = "#2a6496"
SEA_HALO = [effects.withStroke(linewidth=1.2, foreground="#aedcf5")]
RING_PT = 10
DIRECTIONS = 24
MARGIN_PT = 4
CLEARANCE_PT = 2
NEAR, FAR = 14, 40


def two_lines(text: str) -> str | None:
    words = text.split()
    if len(words) < 2:
        return None
    cut = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
    return " ".join(words[:cut]) + "\n" + " ".join(words[cut:])


class WaterLabeller:
    def __init__(self, ax, projection: ccrs.Projection, extent, unit: float, land):
        self.ax, self.projection, self.extent, self.unit = ax, projection, extent, unit
        self.land, self.prepared = land, prep(land)

    def deg(self, points: float) -> float:
        return points * self.unit / METRES_PER_DEGREE

    def fits_card(self, x: float, y: float, half_width_pt: float, half_height_pt: float) -> bool:
        px, py = self.projection.transform_point(x, y, GEODETIC)
        xmin, xmax, ymin, ymax = self.extent.bounds
        dx, dy = half_width_pt * self.unit, half_height_pt * self.unit
        return xmin + dx <= px <= xmax - dx and ymin + dy <= py <= ymax - dy

    def place(self, near: Point, name: str, toward: float = 0.0, inside=None, reach: int | None = None):
        wrapped = two_lines(name) or name
        attempts = ((name, 9.5, NEAR), (wrapped, 9.5, NEAR), (wrapped, 8, NEAR), (wrapped, 6.5, NEAR), (wrapped, 5.5, NEAR), (name, 5.5, NEAR), (wrapped, 5.5, FAR), (name, 5.5, FAR))
        renderer = self.ax.figure.canvas.get_renderer()
        clearance = CLEARANCE_PT * self.ax.figure.dpi / 72
        taken = [other.get_window_extent(renderer).padded(clearance) for other in self.ax.texts if other.get_visible()]
        for text, size, rings in attempts:
            for spot in self._spots(near, text, size, min(rings, reach or rings), toward, 0.04 if size <= 8 and rings == NEAR else 0.0):
                if inside is not None and not inside.contains(Point(spot)):
                    continue
                label = self.ax.text(*spot, text, transform=GEODETIC, fontsize=size, zorder=16, ha="center", va="center",
                                     linespacing=1.05, color=SEA_TEXT, path_effects=SEA_HALO)
                if not any(label.get_window_extent(renderer).overlaps(other) for other in taken):
                    return label
                label.remove()
        return None

    def _spots(self, near: Point, text: str, size: float, rings: int, toward: float, tolerance: float):
        lines = text.split("\n")
        width, height = 0.62 * size * max(len(line) for line in lines), size * 1.5 * len(lines)
        turns = sorted(range(DIRECTIONS), key=lambda k: abs(math.atan2(math.sin(k * 2 * math.pi / DIRECTIONS - toward), math.cos(k * 2 * math.pi / DIRECTIONS - toward))))
        for ring in range(1, rings + 1):
            for turn in turns:
                angle = turn * 2 * math.pi / DIRECTIONS
                x, y = near.x + self.deg(RING_PT) * ring * math.cos(angle), near.y + self.deg(RING_PT) * ring * math.sin(angle)
                if not self.fits_card(x, y, 0.5 * width + MARGIN_PT, 0.5 * height + MARGIN_PT):
                    continue
                half_w = width * self.unit / (METRES_PER_DEGREE * max(math.cos(math.radians(y)), 0.2)) / 2 * 1.15
                half_h = height * self.unit / METRES_PER_DEGREE / 2
                cell = box(x - half_w, y - half_h, x + half_w, y + half_h)
                if not self.prepared.intersects(cell) or (tolerance and self.land.intersection(cell).area < tolerance * cell.area):
                    yield x, y
