from unittest import mock

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest
from shapely.geometry import LineString, Point, box

from geography.features.kinds import Kind
from geography.features.spatial import CRS
from geography.features.surroundings import Linked, SurroundingsResearch
from geography.features.surroundings_paint import outside
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


def test_a_canal_names_only_waters_it_can_outline(research):
    items = [linked("Q20", "Mediterranean Sea", "P2789", classes=("sea",)), linked("Q21", "Tiny Bay", "P2789", classes=("bay",)),
             linked("Q22", "Ghent", "P2789", classes=("city",))]
    research._linked = lambda qid, props: items
    with mock.patch.object(SurroundingsResearch, "shapes", return_value={"Q20": gpd.GeoSeries([box(0, 0, 5, 5)], crs=CRS)}):
        found = research.of(Kind.CANAL, "Q0", line((0, 0), (1, 0)))
    assert [water.name for water in found.waters] == ["Mediterranean Sea"] and not found.rivers


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
    fig, ax, labeller = water_map
    first = labeller.place(Point(1, 0), "Mediterranean Sea")
    second = labeller.place(Point(1, 0), "Red Sea")
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    assert not first.get_window_extent(renderer).overlaps(second.get_window_extent(renderer))
