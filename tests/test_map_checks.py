import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from geography.map_checks import label_problems, visibility_problems


@pytest.fixture
def figure():
    fig = plt.figure(figsize=(10, 5.26), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1000)
    ax.set_ylim(0, 526)
    yield fig, ax
    plt.close(fig)


def drawn(fig):
    fig.canvas.draw()
    return fig


def test_clear_labels_pass(figure):
    fig, ax = figure
    ax.text(200, 200, "France")
    ax.text(600, 400, "Spain")
    assert label_problems(drawn(fig)) == []


def test_overlapping_labels_are_found(figure):
    fig, ax = figure
    ax.text(200, 200, "Netherlands", fontsize=12)
    ax.text(210, 202, "Belgium", fontsize=12)
    assert label_problems(drawn(fig)) == ["'Netherlands' overlaps 'Belgium'"]


def test_a_cut_off_label_is_found(figure):
    fig, ax = figure
    ax.text(990, 200, "Kazakhstan", fontsize=12)
    assert label_problems(drawn(fig)) == ["'Kazakhstan' is cut off"]


def test_a_label_over_the_inset_is_found(figure):
    fig, ax = figure
    fig.add_axes([0.0, 0.0, 0.3, 0.3])
    ax.text(50, 50, "Atlantic Ocean", fontsize=12)
    assert label_problems(drawn(fig)) == ["'Atlantic Ocean' covers the inset"]


def test_a_label_over_protected_points_is_found(figure):
    fig, ax = figure
    ax.text(500, 263, "Rotterdam", fontsize=12, ha="center", va="center")
    river = ax.transData.transform([(500, 263)])
    assert label_problems(drawn(fig), river, "the river") == ["'Rotterdam' covers the river"]


def test_hidden_labels_are_ignored(figure):
    fig, ax = figure
    ax.text(200, 200, "Answer", fontsize=12).set_visible(False)
    ax.text(205, 200, "Belgium", fontsize=12)
    assert label_problems(drawn(fig)) == []


def test_a_feature_outside_the_map_is_found(figure):
    fig, _ = figure
    assert visibility_problems(drawn(fig), (2000, 2000, 2100, 2100)) == ["the feature is outside the map"]


def test_a_dot_needs_a_magnifier(figure):
    fig, _ = figure
    assert visibility_problems(drawn(fig), (500, 263, 500.5, 263.5))
    fig.add_axes([0.7, 0.7, 0.2, 0.2])
    fig.add_axes([0.0, 0.0, 0.2, 0.2])
    assert visibility_problems(drawn(fig), (500, 263, 500.5, 263.5)) == []


def test_a_visible_feature_passes(figure):
    fig, _ = figure
    assert visibility_problems(drawn(fig), np.array((100, 100, 300, 200))) == []
