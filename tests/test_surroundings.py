from unittest import mock

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.patches
import matplotlib.pyplot as plt
import numpy as np
import pytest
from shapely.geometry import LineString, Point, box

from geography.features.kinds import Kind
from geography.features.spatial import CRS
from geography.features.surroundings import (
    Linked,
    Surroundings,
    SurroundingsResearch,
    Waterway,
)
from geography.features.surroundings_paint import SurroundingsPainter, outside
from geography.features.water_labels import WaterLabeller
from geography.render import Extent


def linked(qid, name, prop, links=10, classes=("river",)):
    return Linked(qid, name, prop, links, None, frozenset(classes))


def line(*points) -> gpd.GeoSeries:
    return gpd.GeoSeries([LineString(points)], crs=CRS)


@pytest.fixture
def research():
    found = SurroundingsResearch.__new__(SurroundingsResearch)
    found._traced = lambda qids: {}
    found._parts = lambda qid: []
    return found


def test_rivers_and_waters_are_told_apart():
    assert linked("Q1", "Nile", "P201").is_river
    assert linked("Q2", "Red Sea", "P2789", classes=("sea",)).is_water
    city = linked("Q3", "Ghent", "P2789", classes=("city",))
    assert not city.is_river and not city.is_water


def test_a_lake_shows_its_outflow_first_then_its_best_known_inflows(research):
    items = [linked("Q10", "Small", "P200", 3), linked("Q11", "Big", "P200", 90), linked("Q12", "Out", "P201", 5),
             linked("Q13", "Middle", "P200", 40), linked("Q14", "Undrawn", "P200", 80), linked("Q15", "Fourth", "P200", 20)]
    shapes = {item.qid: line((0, 0), (1, 1)) for item in items if item.qid != "Q14"}
    research._linked = lambda qid, props: items
    with mock.patch.object(SurroundingsResearch, "shapes", return_value=shapes):
        rivers = research.of(Kind.LAKE, "Q0", line((0, 0), (1, 0))).rivers
    assert [river.name for river in rivers] == ["Out", "Big", "Middle", "Fourth"]


def test_a_canal_names_waters_it_can_outline_or_place(research):
    items = [linked("Q20", "Mediterranean Sea", "P2789", classes=("sea",)), linked("Q21", "Tiny Bay", "P2789", classes=("bay",)),
             Linked("Q23", "Saronic Gulf", "P2789", 10, Point(23.5, 37.7), frozenset({"gulf"})), linked("Q22", "Ghent", "P2789", classes=("city",))]
    research._linked = lambda qid, props: items
    with mock.patch.object(SurroundingsResearch, "shapes", return_value={"Q20": gpd.GeoSeries([box(0, 0, 5, 5)], crs=CRS)}):
        found = research.of(Kind.CANAL, "Q0", line((0, 0), (1, 0)))
    assert [(water.name, water.shape is None) for water in found.waters] == [("Mediterranean Sea", False), ("Saronic Gulf", True)]
    assert not found.rivers


def test_a_sea_is_outlined_with_its_parts(research):
    research._linked = lambda qid, props: [linked("Q97", "Atlantic Ocean", "P2789", classes=("ocean",))]
    research._parts = lambda qid: ["Q1247", "Q999"]
    shapes = {"Q97": gpd.GeoSeries([box(-40, 0, -20, 20)], crs=CRS), "Q1247": gpd.GeoSeries([box(-85, 9, -60, 20)], crs=CRS)}
    with mock.patch.object(SurroundingsResearch, "shapes", return_value=shapes):
        atlantic = research.of(Kind.CANAL, "Q0", line((-80, 9), (-79.5, 9.3))).waters[0]
    assert atlantic.shape.union_all().contains(Point(-75, 12))


def test_a_waterfall_without_a_linked_river_takes_the_nearest_one(research):
    research._linked = lambda qid, props: []
    rivers = gpd.GeoDataFrame({"name": ["Rhin", "Elsewhere"], "name_en": ["Rhine", "Elsewhere"]},
                              geometry=[LineString([(8.6, 47.67), (8.7, 47.68)]), LineString([(20, 20), (21, 21)])], crs=CRS)
    with mock.patch.object(SurroundingsResearch, "rivers", return_value=rivers):
        found = research.of(Kind.WATERFALL, "Q0", gpd.GeoSeries([Point(8.615, 47.677)], crs=CRS))
    assert [river.name for river in found.rivers] == ["Rhine"]


def test_other_kinds_get_no_surroundings(research):
    assert not research.of(Kind.MOUNTAIN, "Q0", gpd.GeoSeries([Point(0, 0)], crs=CRS))


def test_rivers_stop_at_the_lake_shore():
    lake = box(0, 0, 2, 2)
    clipped = outside(line((-1, 1), (3, 1)), lake)
    assert clipped.union_all().intersection(lake.buffer(-1e-9)).is_empty
    assert sum(part.length for part in clipped) == pytest.approx(2.0)


@pytest.fixture
def water_map():
    fig = plt.figure(figsize=(10, 5.26), dpi=100)
    projection = ccrs.LambertAzimuthalEqualArea()
    ax = fig.add_axes([0, 0, 1, 1], projection=projection)
    extent = Extent(4_400_000, 2_300_000, 0, 0)
    ax.set_extent(extent.bounds, crs=projection)
    land = box(-25, -15, 0, 15)
    yield fig, ax, WaterLabeller(ax, projection, extent, extent.width / (10 * 72), land)
    plt.close(fig)


def test_a_water_name_stays_inside_its_own_water(water_map):
    _, _, labeller = water_map
    northern = box(0, 5, 20, 10.5)
    label = labeller.place(Point(1, 0), "Northern Sea", 0.0, northern)
    assert label is not None and northern.contains(Point(label.get_position()))


def test_water_names_do_not_land_on_each_other(water_map):
    fig, _, labeller = water_map
    first = labeller.place(Point(1, 0), "Mediterranean Sea")
    second = labeller.place(Point(1, 0), "Red Sea")
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    assert not first.get_window_extent(renderer).overlaps(second.get_window_extent(renderer))


def test_waterfalls_and_canyons_carry_their_icon(research):
    research._linked = lambda qid, props: [linked("Q30", "Zambezi", "P206")]
    with mock.patch.object(SurroundingsResearch, "shapes", return_value={"Q30": line((25, -18), (26, -17.9))}):
        assert research.of(Kind.WATERFALL, "Q0", gpd.GeoSeries([Point(25.85, -17.92)], crs=CRS)).landmark is Kind.WATERFALL


@pytest.fixture
def marked_map():
    fig = plt.figure(figsize=(10, 5.26), dpi=100)
    projection = ccrs.LambertAzimuthalEqualArea()
    ax = fig.add_axes([0, 0, 1, 1], projection=projection)
    extent = Extent(4_400_000, 2_300_000, 0, 0)
    ax.set_extent(extent.bounds, crs=projection)
    yield fig, ax, projection, extent
    plt.close(fig)


def icon_bounds(ax, before: int):
    vertices = np.concatenate([patch.get_path().transformed(patch.get_patch_transform()).vertices for patch in ax.patches[before:]])
    return ax.transData.transform(vertices)


@pytest.mark.parametrize(("radius_in", "inside"), [(0.12, False), (0.6, True)])
def test_a_landmark_icon_sits_inside_a_large_marker_and_below_a_small_one(marked_map, radius_in, inside):
    fig, ax, projection, extent = marked_map
    centre = ax.transData.transform(projection.transform_point(0, 0, ccrs.PlateCarree())) / fig.dpi
    fig.add_artist(matplotlib.patches.Circle(tuple(centre), radius_in, fill=False, transform=fig.dpi_scale_trans))
    before = len(ax.patches)
    SurroundingsPainter(Surroundings(landmark=Kind.WATERFALL), gpd.GeoSeries([Point(0, 0)], crs=CRS), None, fig, projection, extent).paint()
    points = icon_bounds(ax, before)
    circle_bottom = (centre[1] - radius_in) * fig.dpi
    if inside:
        assert points[:, 1].min() >= circle_bottom
    else:
        assert points[:, 1].max() <= circle_bottom + 1


class Base:
    def __init__(self, land):
        self.data = type("Data", (), {"countries": gpd.GeoSeries([land], crs=CRS)})()


@pytest.mark.parametrize(("point", "named"), [(Point(5, 0), True), (Point(-5, 0), False)])
def test_a_water_without_an_outline_is_named_at_its_point_only_if_that_is_water(marked_map, point, named):
    fig, ax, projection, extent = marked_map
    surroundings = Surroundings(waters=(Waterway("Saronic Gulf", None, point),))
    SurroundingsPainter(surroundings, line((0, -1), (0, 1)), Base(box(-25, -15, 0, 15)), fig, projection, extent).paint()
    texts = [text for text in ax.texts if text.get_text().replace("\n", " ") == "Saronic Gulf"]
    assert bool(texts) is named
    if named:
        x, y = texts[0].get_position()
        assert Point(x, y).distance(point) < 3
