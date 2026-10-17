from dataclasses import dataclass

import cartopy.crs as ccrs
from matplotlib.artist import Artist
from matplotlib.lines import Line2D
from matplotlib.transforms import Bbox

MARKER_COLOR = "#1f5fa8"
MARKER_SIZE = 40
CAPITAL_SCALE = 6.5
MARKER_EDGE = "white"
LABEL_COLOR = "#262626"
LABEL_FILL = "white"
LABEL_EDGE = "#9a9aa0"
LABEL_SIZE = 13
LABEL_OPACITY = 0.93
PILL = "round,pad=0.25,rounding_size=0.5"
MARKER_REACH = 7
LINE_REACH = 4
LINE_STEP = 8
PADDING = 3
INSET_PADDING = 2
FIGURE_MARGIN = 4
OFFSETS = (
    (7, 0, "left", "center"),
    (-7, 0, "right", "center"),
    (0, 7, "center", "bottom"),
    (0, -7, "center", "top"),
    (6, 6, "left", "bottom"),
    (-6, 6, "right", "bottom"),
    (6, -6, "left", "top"),
    (-6, -6, "right", "top"),
    (14, 0, "left", "center"),
    (-14, 0, "right", "center"),
    (0, 15, "center", "bottom"),
    (0, -15, "center", "top"),
)


@dataclass(frozen=True)
class CityLabel:
    name: str
    longitude: float
    latitude: float
    capital: bool = False
    target: bool = False


def overlaps(box: Bbox, others: list[Bbox], padding: float = PADDING) -> bool:
    return any(
        box.x0 - padding < other.x1 and box.x1 + padding > other.x0 and box.y0 - padding < other.y1 and box.y1 + padding > other.y0
        for other in others
    )


class CityLabels:
    def __init__(self, fig, ax, projection: ccrs.Projection, extent, cities: tuple[CityLabel, ...], answer_color: str):
        self.fig = fig
        self.ax = ax
        self.renderer = fig.canvas.get_renderer()
        self.answer_color = answer_color
        self.answer: Artist | None = None
        self.visible = [(city, *projection.transform_point(city.longitude, city.latitude, ccrs.Geodetic())) for city in cities]
        self.visible = [(city, x, y) for city, x, y in self.visible if self._inside(extent, x, y)]
        self.dots = {city.name: self._dot(city, x, y) for city, x, y in self.visible if not city.target}
        self.taken = [self._around(x, y, MARKER_REACH) for _, x, y in self.visible]
        self.lines = self._connector_boxes()
        self.blocked = [other.get_window_extent(self.renderer) for other in fig.axes[1:]]
        self._place()

    def show_answer(self, shown: bool) -> None:
        if self.answer is not None:
            self.answer.set_visible(shown)

    @staticmethod
    def _inside(extent, x: float, y: float) -> bool:
        xmin, xmax, ymin, ymax = extent.bounds
        return xmin < x < xmax and ymin < y < ymax

    def _dot(self, city: CityLabel, x: float, y: float) -> Artist:
        return self.ax.scatter(
            [x],
            [y],
            s=MARKER_SIZE * (CAPITAL_SCALE if city.capital else 1),
            marker="*" if city.capital else "o",
            color=MARKER_COLOR,
            edgecolors=MARKER_EDGE,
            linewidths=0.9,
            zorder=8,
        )

    def _around(self, x: float, y: float, reach: float) -> Bbox:
        px, py = self.ax.transData.transform((x, y))
        return Bbox.from_extents(px - reach, py - reach, px + reach, py + reach)

    def _connector_boxes(self) -> list[Bbox]:
        boxes = []
        for line in (artist for artist in self.fig.artists if isinstance(artist, Line2D)):
            xs, ys = line.get_data()
            points = line.get_transform().transform(list(zip(xs, ys, strict=True)))
            for (x0, y0), (x1, y1) in zip(points[:-1], points[1:], strict=True):
                steps = max(2, int(max(abs(x1 - x0), abs(y1 - y0)) / LINE_STEP))
                for step in range(steps + 1):
                    px, py = x0 + (x1 - x0) * step / steps, y0 + (y1 - y0) * step / steps
                    boxes.append(Bbox.from_extents(px - LINE_REACH, py - LINE_REACH, px + LINE_REACH, py + LINE_REACH))
        return boxes

    def _place(self) -> None:
        ordered = sorted(self.visible, key=lambda item: (not item[0].target, not item[0].capital))
        retry = [item for item in ordered if not self._try(*item, avoid_lines=True)]
        for city, x, y in retry:
            if not self._try(city, x, y, avoid_lines=False) and city.name in self.dots:
                self.dots.pop(city.name).remove()

    def _try(self, city: CityLabel, x: float, y: float, avoid_lines: bool) -> bool:
        for dx, dy, ha, va in OFFSETS:
            annotation = self._annotate(city, x, y, dx, dy, ha, va)
            box = annotation.get_window_extent(self.renderer)
            obstacles = self.taken + (self.lines if avoid_lines else [])
            if self._fits(box) and not overlaps(box, obstacles) and not overlaps(box, self.blocked, INSET_PADDING):
                self.taken.append(box)
                if city.target:
                    self.answer = annotation
                return True
            annotation.remove()
        return False

    def _annotate(self, city: CityLabel, x: float, y: float, dx: float, dy: float, ha: str, va: str) -> Artist:
        pill = dict(
            boxstyle=PILL,
            fc=self.answer_color if city.target else LABEL_FILL,
            ec=self.answer_color if city.target else LABEL_EDGE,
            lw=0.6,
            alpha=LABEL_OPACITY,
        )
        return self.ax.annotate(
            city.name,
            (x, y),
            xytext=(dx, dy),
            textcoords="offset points",
            ha=ha,
            va=va,
            fontsize=LABEL_SIZE + (1 if city.target else 0),
            fontweight="bold",
            color="white" if city.target else LABEL_COLOR,
            zorder=9,
            bbox=pill,
        )

    def _fits(self, box: Bbox) -> bool:
        figure = self.fig.bbox
        return (
            figure.x0 + FIGURE_MARGIN < box.x0
            and box.x1 < figure.x1 - FIGURE_MARGIN
            and figure.y0 + FIGURE_MARGIN < box.y0
            and box.y1 < figure.y1 - FIGURE_MARGIN
        )
