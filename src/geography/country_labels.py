import textwrap

import geopandas as gpd
from matplotlib import patheffects
from matplotlib.text import Text
from matplotlib.transforms import Bbox
from shapely.geometry import box
from shapely.ops import polylabel

from geography.labels import overlaps

TOUCH_M = 30_000
TOUCHING_SIZES = (12, 9)
OTHER_SIZES = (9, 7)
PROVINCE_SIZES = (10, 8, 6.5)
ANSWER_SIZES = (14, 11, 8.5)
TOUCHING_INSIDE = 0.55
OTHER_INSIDE = 0.7
PROVINCE_INSIDE = 0.6
TOUCHING_ON_FOCUS = 0.12
OTHER_ON_FOCUS = 0.02
MIN_VISIBLE_SHARE = 0.5
MIN_FRAME_SHARE = 0.0015
LABEL_PARTS = 3
LETTER_SPACE = " "
LABEL_COLOR = (0.43, 0.4, 0.34)
HALO_COLOR = "#e9dfc7"
ANSWER_COLOR = "white"
ANSWER_HALO = "#5e0f1c"
HALO_WIDTH = 2.2
PADDING = 2
PILL = "round,pad=0.25,rounding_size=0.5"
PILL_COLOR = "#A6192E"
PILL_OFFSETS = ((10, 0, "left", "center"), (-10, 0, "right", "center"), (0, 12, "center", "bottom"), (0, -12, "center", "top"),
                (12, 12, "left", "bottom"), (-12, 12, "right", "bottom"), (12, -12, "left", "top"), (-12, -12, "right", "top"),
                (30, 0, "left", "center"), (-30, 0, "right", "center"), (0, 32, "center", "bottom"), (0, -32, "center", "top"))
FIGURE_MARGIN = 4
POLE_TOLERANCE_M = 1_000


def layouts(name: str) -> list[str]:
    words = name.upper().split()
    width = max(len(" ".join(words)) // 2 + 1, max(len(word) for word in words))
    options = [[" ".join(words)], textwrap.wrap(" ".join(words), width, break_long_words=False), words]
    return list(dict.fromkeys("\n".join(LETTER_SPACE.join(line) for line in lines) for lines in options))


class Placer:
    def __init__(self, fig, ax, obstacles: list[Bbox]):
        self.fig = fig
        self.ax = ax
        self.renderer = fig.canvas.get_renderer()
        self.placed = list(obstacles)

    def place(self, label: str, geometry, avoid, sizes: tuple[float, ...], inside: float, on_avoid: float, color=LABEL_COLOR, halo=HALO_COLOR) -> Text | None:
        parts = sorted(getattr(geometry, "geoms", [geometry]), key=lambda part: part.area, reverse=True)
        to_data = self.ax.transData.inverted()
        for size in sizes:
            for layout in layouts(label):
                for part in parts[:LABEL_PARTS]:
                    if part.geom_type != "Polygon":
                        continue
                    point = polylabel(part, tolerance=POLE_TOLERANCE_M)
                    text = self.ax.text(
                        point.x, point.y, layout, fontsize=size, fontweight="bold", color=color, ha="center", va="center",
                        linespacing=1.1, zorder=4, path_effects=[patheffects.withStroke(linewidth=HALO_WIDTH, foreground=halo)],
                    )
                    extent = text.get_window_extent(self.renderer)
                    (x0, y0), (x1, y1) = to_data.transform([(extent.x0, extent.y0), (extent.x1, extent.y1)])
                    area = box(x0, y0, x1, y1)
                    if (
                        area.intersection(geometry).area / area.area >= inside
                        and (avoid is None or area.intersection(avoid).area / area.area <= on_avoid)
                        and self._fits(extent)
                        and not overlaps(extent, self.placed, PADDING)
                    ):
                        self.placed.append(extent)
                        return text
                    text.remove()
        return None

    def free(self, extent: Bbox) -> bool:
        return self._fits(extent) and not overlaps(extent, self.placed, PADDING)

    def _fits(self, extent: Bbox) -> bool:
        figure = self.fig.bbox
        return (
            figure.x0 + FIGURE_MARGIN < extent.x0
            and extent.x1 < figure.x1 - FIGURE_MARGIN
            and figure.y0 + FIGURE_MARGIN < extent.y0
            and extent.y1 < figure.y1 - FIGURE_MARGIN
        )


class CountryLabels:
    def __init__(self, placer: Placer, extent, neighbours: gpd.GeoDataFrame, focus):
        xmin, xmax, ymin, ymax = extent.bounds
        frame = box(xmin, ymin, xmax, ymax)
        visible = neighbours.assign(whole=neighbours.geometry, geometry=neighbours.geometry.intersection(frame))
        visible = visible[~visible.geometry.is_empty]
        visible = visible.assign(
            touching=visible.geometry.buffer(TOUCH_M).intersects(focus),
            in_view=visible.geometry.area / visible["whole"].area,
            share=visible.geometry.area / frame.area,
        )
        visible = visible[visible.touching | ((visible.in_view >= MIN_VISIBLE_SHARE) & (visible.share >= MIN_FRAME_SHARE))]
        for row in visible.assign(size=visible.geometry.area).sort_values(["touching", "size"], ascending=False).itertuples():
            sizes, inside, on_focus = (
                (TOUCHING_SIZES, TOUCHING_INSIDE, TOUCHING_ON_FOCUS) if row.touching else (OTHER_SIZES, OTHER_INSIDE, OTHER_ON_FOCUS)
            )
            placer.place(row.label, row.geometry, focus, sizes, inside, on_focus)


class ProvinceLabels:
    def __init__(self, placer: Placer, extent, provinces: gpd.GeoDataFrame, target):
        xmin, xmax, ymin, ymax = extent.bounds
        frame = box(xmin, ymin, xmax, ymax)
        visible = provinces.assign(geometry=provinces.geometry.intersection(frame))
        for row in visible[~visible.geometry.is_empty].assign(size=lambda frame_: frame_.geometry.area).sort_values("size", ascending=False).itertuples():
            placer.place(row.label, row.geometry, target, PROVINCE_SIZES, PROVINCE_INSIDE, OTHER_ON_FOCUS)


class AnswerLabel:
    def __init__(self, placer: Placer, extent, label: str, target):
        xmin, xmax, ymin, ymax = extent.bounds
        visible = target.intersection(box(xmin, ymin, xmax, ymax))
        self.text = placer.place(label, visible, None, ANSWER_SIZES, TOUCHING_INSIDE, 1.0, ANSWER_COLOR, ANSWER_HALO) or self._pill(placer, label, visible)

    @staticmethod
    def _pill(placer: Placer, label: str, visible):
        point = visible.representative_point()
        style = {"boxstyle": PILL, "fc": PILL_COLOR, "ec": PILL_COLOR}
        for dx, dy, ha, va in PILL_OFFSETS:
            note = placer.ax.annotate(label, (point.x, point.y), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va, fontsize=12,
                                      fontweight="bold", color="white", zorder=9, bbox=style)
            extent = note.get_window_extent(placer.renderer)
            if placer.free(extent):
                placer.placed.append(extent)
                return note
            note.remove()
        return None

    def show(self, shown: bool) -> None:
        if self.text is not None:
            self.text.set_visible(shown)
