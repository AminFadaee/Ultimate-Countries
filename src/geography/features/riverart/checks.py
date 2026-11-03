import math
from dataclasses import dataclass, field

import numpy as np
from matplotlib.transforms import Bbox
from shapely.geometry import Point

from geography.features.riverart.kinds import MouthKind, SourceKind
from geography.features.riverart.paint import CHEVRON_MIN_GAP, RiverPainter, paint_river
from geography.map_checks import label_problems

MIN_RIVER_SHARE = 0.15
MAX_GAP_PT = 12
MAX_LABEL_REACH_PT = 40
MAX_SEA_LABEL_REACH_PT = 110
SOURCE_LABEL_REACH_PT = 20
MIN_MOUTH_LABEL_PT = 7


def gap_to_box(box: Bbox, x: float, y: float) -> float:
    return math.hypot(max(box.x0 - x, 0, x - box.x1), max(box.y0 - y, 0, y - box.y1))


@dataclass
class RiverCheck:
    art: object
    painter: RiverPainter | None = None
    problems: list[str] = field(default_factory=list)

    def paint(self, renderer, fig, projection, extent) -> None:
        self.painter = paint_river(self.art, renderer, fig, projection, extent)

    def inspect(self, fig) -> None:
        painter = self.painter
        self.points = fig.dpi / 72
        self.renderer = fig.canvas.get_renderer()
        self.path = painter.ax.transData.transform(painter.xy)
        self.problems = [*self._size(fig), *self._source(), *self._mouth(), *self._chevrons(), *self._labels(fig)]

    def _pt(self, pixels: float) -> float:
        return pixels / self.points

    def _display(self, point: Point) -> np.ndarray:
        return self.painter.ax.transData.transform(self.painter.project(point.x, point.y))

    def _size(self, fig) -> list[str]:
        share = (np.ptp(self.path, axis=0) / (fig.get_size_inches() * fig.dpi)).max()
        if share < MIN_RIVER_SHARE:
            return [f"river spans only {share:.0%} of the card"]
        return []

    def _source(self) -> list[str]:
        painter, art = self.painter, self.art
        start = self.path[0]
        if art.source is None:
            if self._linked_at(Point(art.path.coords[0])):
                return []
            return ["no source in the data"]
        if painter.kind in (SourceKind.HIGHLAND, SourceKind.GLACIER) and painter.peaks:
            x, y, size = painter.nearest_peak()
            gap = max(self._pt(float(np.linalg.norm(self._display(Point(x, y)) - start))) - size, 0)
        elif painter.kind is SourceKind.LAKE and art.source_lake is not None:
            lake = art.source_lake.to_crs(painter.projection.proj4_init).union_all()
            gap = lake.distance(Point(painter.xy[0])) / painter.unit
        elif painter.kind is SourceKind.UNKNOWN:
            gap = self._pt(float(np.linalg.norm(self._display(painter.source_point) - start)))
        else:
            gap = self._pt(float(np.linalg.norm(self._display(painter.anchor()) - start)))
        problems = [f"source is {gap:.0f} pt from the river"] if gap > MAX_GAP_PT else []
        return problems + self._source_label()

    def _source_label(self) -> list[str]:
        painter = self.painter
        labels = [label for label in painter.source_labels if label.get_visible()]
        if painter.kind is SourceKind.LAKE:
            return []
        if not labels:
            return ["source is not labelled"]
        if painter.kind in (SourceKind.HIGHLAND, SourceKind.GLACIER) and painter.peaks:
            x, y, size = painter.nearest_peak()
            spot, allowance = self._display(Point(x, y)), size * 0.7 + SOURCE_LABEL_REACH_PT
        else:
            spot = self._display(painter.anchor())
            allowance = (painter._icon_size() if painter.kind.has_icon else 0) + MAX_LABEL_REACH_PT
        gap = min(gap_to_box(label.get_window_extent(self.renderer), *spot) for label in labels) / self.points
        return [f"source label is {gap:.0f} pt from the source"] if gap > allowance else []

    def _linked_at(self, point: Point) -> bool:
        reach = self.painter.deg(MAX_GAP_PT)
        return any(lines.union_all().distance(point) <= reach for lines in self.art.linked.values())

    def _mouth(self) -> list[str]:
        art, painter = self.art, self.painter
        if art.mouth is None:
            return ["no mouth in the data"]
        problems = []
        end = self.path[-1]
        labels = [label for label in painter.mouth_labels if label.get_visible()]
        if not labels:
            problems.append("mouth is not labelled")
        elif min(label.get_fontsize() for label in labels) < MIN_MOUTH_LABEL_PT:
            problems.append(f"mouth label is only {min(label.get_fontsize() for label in labels):g} pt")
        elif min(gap_to_box(label.get_window_extent(self.renderer), *end) for label in labels) > (self._mouth_reach() + painter.mouth_symbol_pt) * self.points:
            problems.append("mouth label is far from the river's end")
        reach = painter.deg(MAX_GAP_PT)
        mouth_end = Point(art.path.coords[-1])
        if art.mouth.kind in (MouthKind.SEA, MouthKind.LAKE) and not painter._mouth_inland():
            land = painter.renderer.data.countries.geometry.union_all()
            if land.contains(mouth_end.buffer(reach)):
                problems.append("river ends away from the water it flows into")
        if art.mouth.kind is MouthKind.RIVER:
            target = art.linked.get(art.mouth.name)
            if target is None:
                problems.append("the river it flows into is not drawn")
            elif target.union_all().distance(mouth_end) > reach:
                problems.append("river ends away from the river it flows into")
        return problems

    def _mouth_reach(self) -> float:
        sea = self.art.mouth.kind is MouthKind.SEA and not self.painter._mouth_inland()
        return MAX_SEA_LABEL_REACH_PT if sea else MAX_LABEL_REACH_PT

    def _chevrons(self) -> list[str]:
        spots = sorted(self.painter.chevrons)
        gaps = np.diff(spots)
        if len(gaps) and gaps.min() < CHEVRON_MIN_GAP:
            return [f"flow arrows only {gaps.min():.0f} pt apart"]
        return []

    def _labels(self, fig) -> list[str]:
        return label_problems(fig, self.path, "the river")
