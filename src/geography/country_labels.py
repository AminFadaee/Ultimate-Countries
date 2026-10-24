import textwrap

import geopandas as gpd
from matplotlib import patheffects
from matplotlib.transforms import Bbox
from shapely.geometry import box
from shapely.ops import polylabel

from geography.labels import overlaps

TOUCH_M = 30_000
TOUCHING_SIZES = (12, 9)
OTHER_SIZES = (9, 7)
TOUCHING_INSIDE = 0.55
OTHER_INSIDE = 0.7
TOUCHING_ON_FOCUS = 0.12
OTHER_ON_FOCUS = 0.02
MIN_VISIBLE_SHARE = 0.5
MIN_FRAME_SHARE = 0.0015
LABEL_PARTS = 3
LETTER_SPACE = " "
LABEL_COLOR = (0.43, 0.4, 0.34)
HALO_COLOR = "#e9dfc7"
HALO_WIDTH = 2.2
PADDING = 2
FIGURE_MARGIN = 4
POLE_TOLERANCE_M = 1_000


def layouts(name: str) -> list[str]:
    words = name.upper().split()
    width = max(len(" ".join(words)) // 2 + 1, max(len(word) for word in words))
    options = [[" ".join(words)], textwrap.wrap(" ".join(words), width, break_long_words=False), words]
    return list(dict.fromkeys("\n".join(LETTER_SPACE.join(line) for line in lines) for lines in options))


class CountryLabels:
    def __init__(self, fig, ax, extent, neighbours: gpd.GeoDataFrame, focus, obstacles: list[Bbox]):
        self.fig = fig
        self.ax = ax
        self.renderer = fig.canvas.get_renderer()
        self.focus = focus
        self.placed = list(obstacles)
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
            self._place(row)

    def _place(self, row) -> None:
        sizes, inside, on_focus = (
            (TOUCHING_SIZES, TOUCHING_INSIDE, TOUCHING_ON_FOCUS) if row.touching else (OTHER_SIZES, OTHER_INSIDE, OTHER_ON_FOCUS)
        )
        parts = sorted(getattr(row.geometry, "geoms", [row.geometry]), key=lambda part: part.area, reverse=True)
        to_data = self.ax.transData.inverted()
        for size in sizes:
            for layout in layouts(row.label):
                for part in parts[:LABEL_PARTS]:
                    if part.geom_type != "Polygon":
                        continue
                    point = polylabel(part, tolerance=POLE_TOLERANCE_M)
                    text = self.ax.text(
                        point.x, point.y, layout, fontsize=size, fontweight="bold", color=LABEL_COLOR, ha="center", va="center",
                        linespacing=1.1, zorder=4, path_effects=[patheffects.withStroke(linewidth=HALO_WIDTH, foreground=HALO_COLOR)],
                    )
                    extent = text.get_window_extent(self.renderer)
                    (x0, y0), (x1, y1) = to_data.transform([(extent.x0, extent.y0), (extent.x1, extent.y1)])
                    area = box(x0, y0, x1, y1)
                    if (
                        area.intersection(row.geometry).area / area.area >= inside
                        and area.intersection(self.focus).area / area.area <= on_focus
                        and self._fits(extent)
                        and not overlaps(extent, self.placed, PADDING)
                    ):
                        self.placed.append(extent)
                        return
                    text.remove()

    def _fits(self, box_: Bbox) -> bool:
        figure = self.fig.bbox
        return figure.x0 + FIGURE_MARGIN < box_.x0 and box_.x1 < figure.x1 - FIGURE_MARGIN and figure.y0 + FIGURE_MARGIN < box_.y0 and box_.y1 < figure.y1 - FIGURE_MARGIN
