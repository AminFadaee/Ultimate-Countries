from itertools import combinations

import numpy as np
from matplotlib.figure import Figure
from matplotlib.transforms import Bbox

OVERLAP_PT = 1
MIN_VISIBLE_PT = 4


def written(ax) -> dict:
    renderer = ax.figure.canvas.get_renderer()
    return {text: text.get_window_extent(renderer) for text in ax.texts if text.get_visible() and text.get_text().strip()}


def name_of(text) -> str:
    return text.get_text().replace("\n", " ")


def label_problems(fig: Figure, protected: np.ndarray | None = None, protected_name: str = "the feature") -> list[str]:
    renderer = fig.canvas.get_renderer()
    boxes = written(fig.axes[0])
    figure = fig.bbox
    others = [axes.get_window_extent(renderer) for axes in fig.axes[1:]]
    problems = []
    for text, box in boxes.items():
        if box.x0 < figure.x0 or box.y0 < figure.y0 or box.x1 > figure.x1 or box.y1 > figure.y1:
            problems.append(f"'{name_of(text)}' is cut off")
        if any(box.overlaps(other) for other in others):
            problems.append(f"'{name_of(text)}' covers the inset")
        if protected is not None and len(protected) and covers(box, protected):
            problems.append(f"'{name_of(text)}' covers {protected_name}")
    pad = OVERLAP_PT * fig.dpi / 72
    for (one, a), (two, b) in combinations(boxes.items(), 2):
        if a.x0 + pad < b.x1 and b.x0 + pad < a.x1 and a.y0 + pad < b.y1 and b.y0 + pad < a.y1:
            problems.append(f"'{name_of(one)}' overlaps '{name_of(two)}'")
    return problems


def covers(box: Bbox, points: np.ndarray) -> bool:
    return bool(((points[:, 0] > box.x0) & (points[:, 0] < box.x1) & (points[:, 1] > box.y0) & (points[:, 1] < box.y1)).any())


def visibility_problems(fig: Figure, bounds: tuple[float, float, float, float]) -> list[str]:
    ax = fig.axes[0]
    (x0, y0), (x1, y1) = ax.transData.transform([bounds[:2], bounds[2:]])
    shown = ax.get_window_extent(fig.canvas.get_renderer())
    if x1 < shown.x0 or x0 > shown.x1 or y1 < shown.y0 or y0 > shown.y1:
        return ["the feature is outside the map"]
    size = max(abs(x1 - x0), abs(y1 - y0)) * 72 / fig.dpi
    magnified = len(fig.axes) > 2
    return [] if size >= MIN_VISIBLE_PT or magnified else [f"the feature is only {size:.1f} pt across, with no magnifier"]
