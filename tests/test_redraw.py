from unittest import mock

import geopandas as gpd
from shapely.geometry import LineString, Point, box

from geography.features import collect
from geography.features.collect import PlacesCollector, PlacesPaths, document, redrawn
from geography.features.kinds import Kind
from geography.features.model import Feature
from geography.features.spatial import CRS


def feature(**extras) -> Feature:
    found = Feature("Q1", "Lake", Kind.LAKE, gpd.GeoSeries([box(0, 0, 1, 1)], crs=CRS), qid="Q1", map="places/maps/lake.png")
    found.extras.update(extras)
    return found


def test_a_redraw_changes_only_the_researched_fields_and_keeps_the_export_order():
    saved = document(feature(group="Lakes", surroundings={"rivers": ["Old"]}, river={"stale": True}))
    fresh = feature(group="Lakes", surroundings={"rivers": ["New"]})
    fresh.map = None
    assert redrawn(saved, fresh) == document(fresh)
    assert list(redrawn(saved, fresh)) == list(document(fresh))


def test_saved_shapes_load_back_part_by_part(tmp_path):
    shapes = [Feature("Q1", "River", Kind.RIVER, gpd.GeoSeries([LineString([(0, 0), (1, 1)]), LineString([(1, 1), (2, 0)])], crs=CRS)),
              Feature("Q2", "Peak", Kind.MOUNTAIN, gpd.GeoSeries([Point(5, 5)], crs=CRS))]
    with mock.patch.object(collect, "SHAPES_DIR", tmp_path):
        collector = PlacesCollector(PlacesPaths(tmp_path / "data"), None)
        collector._save_shapes(shapes)
        loaded = collector._load_shapes()
    assert {key: list(series) for key, series in loaded.items()} == {shape.key: list(shape.geometry) for shape in shapes}
