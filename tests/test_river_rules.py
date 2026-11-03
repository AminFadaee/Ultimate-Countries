import re

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiPoint, Point, box

from geography.countrydeck.places import drawn_enough
from geography.features.kinds import Kind
from geography.features.model import Feature
from geography.features.riverart import geometry as river_geometry
from geography.features.riverart.facts import RiverResearch, oversized
from geography.features.riverart.infobox import InfoboxLinks, InfoboxSource
from geography.features.riverart.kinds import SourceKind, source_kind, source_label
from geography.features.spatial import CRS

NORMAL = "http://wikiba.se/ontology#NormalRank"
PREFERRED = "http://wikiba.se/ontology#PreferredRank"


def research(rows=None, items=None) -> RiverResearch:
    found = RiverResearch.__new__(RiverResearch)
    found.native = None
    found._rows = lambda query: rows(query) if rows else []
    if items is not None:
        found._items = items
    return found


@pytest.mark.parametrize(("classes", "kind"), [
    ({"ortsteil"}, SourceKind.VILLAGE),
    ({"locality"}, SourceKind.VILLAGE),
    ({"canal"}, SourceKind.CANAL),
    ({"glacier"}, SourceKind.GLACIER),
    ({"mountain range"}, SourceKind.HIGHLAND),
    ({"spring"}, SourceKind.SPRING),
    ({"big city"}, SourceKind.CITY),
    ({"region"}, SourceKind.UNKNOWN),
])
def test_source_kind_from_classes(classes, kind):
    assert source_kind(classes, False) is kind


def test_a_name_that_only_describes_the_source_becomes_its_type():
    assert source_label("source of the Seine River", {"spring"}) == "Spring"
    assert source_label("Chemayungdung glacier", {"glacier"}) == "Chemayungdung glacier"


def mouth_rows(*mouths):
    def rows(query):
        if "p:P403" not in query:
            return []
        return [{"m": f"http://www.wikidata.org/entity/{qid}", "ml": label, "rank": rank, "c": f"Point({x} {y})"} for qid, label, rank, (x, y) in mouths]
    return rows


def test_the_mouth_nearest_the_river_ends_wins_over_a_river_it_passes():
    volga_ends = MultiPoint([(32.6, 57.25), (47.9, 45.8)])
    found = research(mouth_rows(("Q79082", "Kama", NORMAL, (49.99, 55.36)), ("Q5484", "Caspian Sea", NORMAL, (50.5, 42.0))))
    row, _ = found._mouth_row("Q626", [], volga_ends)
    assert row["ml"] == "Caspian Sea"


def test_a_preferred_mouth_wins():
    ends = MultiPoint([(0, 0), (10, 0)])
    found = research(mouth_rows(("Q1", "Near", NORMAL, (10, 0)), ("Q2", "Preferred", PREFERRED, (40, 40))))
    assert found._mouth_row("Q0", [], ends)[0]["ml"] == "Preferred"


def test_the_mouth_choice_does_not_depend_on_row_order():
    ends = MultiPoint([(0, 0), (10, 0)])
    mouths = [("Q1", "Lagoon", NORMAL, (10.2, 0)), ("Q2", "Sea", NORMAL, (14, 3))]
    first = research(mouth_rows(*mouths))._mouth_row("Q0", [], ends)[0]["ml"]
    second = research(mouth_rows(*reversed(mouths)))._mouth_row("Q0", [], ends)[0]["ml"]
    assert first == second == "Lagoon"


def test_a_river_is_cut_where_it_meets_the_river_it_flows_into():
    path = LineString([(0, 0), (5, 0), (8, 0), (12, 0)])
    river = gpd.GeoSeries([path], crs=CRS)
    mouth_river = gpd.GeoSeries([LineString([(8, 0), (14, 0)])], crs=CRS)
    trimmed_river, trimmed_path = river_geometry.trimmed_at_confluence(river, path, mouth_river)
    assert trimmed_path.coords[-1][0] == pytest.approx(8.0)
    assert trimmed_river.total_bounds[2] <= 8.0 + river_geometry.STUB_MARGIN + 1e-9


def test_a_river_that_only_touches_its_mouth_river_at_the_end_is_not_cut():
    path = LineString([(0, 0), (5, 0), (10, 0)])
    mouth_river = gpd.GeoSeries([LineString([(10, -5), (10, 5)])], crs=CRS)
    assert river_geometry.trimmed_at_confluence(gpd.GeoSeries([path], crs=CRS), path, mouth_river) is None


def river_feature(line: LineString | None) -> Feature:
    geometry = gpd.GeoSeries([line] if line else [Point(5, 52)], crs=CRS)
    return Feature("Q1", "River", Kind.RIVER, geometry)


def test_rivers_drawn_under_their_share_of_length_are_dropped():
    ten_km = LineString([(5.0, 52.0), (5.0, 52.09)])
    assert drawn_enough(river_feature(ten_km), 20)
    assert not drawn_enough(river_feature(ten_km), 40)
    assert not drawn_enough(river_feature(None), 515)
    assert drawn_enough(river_feature(ten_km), None)


def test_a_range_much_larger_than_its_river_is_oversized():
    river = gpd.GeoSeries([LineString([(49, 36.5), (50, 37.5)])], crs=CRS)
    assert oversized(gpd.GeoSeries([box(45, 26, 57, 37)], crs=CRS), river)
    assert not oversized(gpd.GeoSeries([box(48.8, 36.3, 49.4, 36.9)], crs=CRS), river)


def test_rivers_meeting_at_the_start_become_the_source():
    rivers = [{"label": "Shilka", "classes": {"river"}, "c": "Point(121.47 53.33)"}, {"label": "Onon", "classes": {"river"}, "c": "Point(115.8 51.7)"}]
    source = RiverResearch._meeting(rivers, {}, Point(121.49, 53.33))
    assert source.name == "Shilka" and source.kind is SourceKind.UNKNOWN


def test_a_drawn_source_river_at_the_start_needs_no_meeting_marker():
    rivers = [{"label": "Shilka", "classes": {"river"}, "c": "Point(121.47 53.33)"}]
    drawn = {"Shilka": gpd.GeoSeries([LineString([(121.0, 53.0), (121.49, 53.33)])], crs=CRS)}
    assert RiverResearch._meeting(rivers, drawn, Point(121.49, 53.33)) is None


def test_a_native_source_more_than_100_km_from_the_english_coordinates_is_dropped():
    links = InfoboxLinks((), (InfoboxSource((), (), (59.0, 37.17)),))
    assert RiverResearch._agrees({"c": "Point(59.36 36.97)"}, links)
    assert not RiverResearch._agrees({"c": "Point(55.0 35.0)"}, links)


def listed(query: str) -> list[dict]:
    return [{"label": qid, "qid": qid, "c": None, "classes": set(), "links": 0, "uri": qid} for qid in re.findall(r"wd:(Q\d+)", query) if "VALUES" in query]


def test_source_order_linked_then_native_then_written_then_location():
    english = InfoboxLinks((), (
        InfoboxSource(("Chemayungdung glacier",), (), (82.1, 30.3), "1"),
        InfoboxSource(("Manasarovar",), ("Q233627",), (82.1, 30.3), "1", located=True),
    ))
    found = research(items=lambda anchor: listed(anchor))
    found._native_links = lambda qid: InfoboxLinks((), (InfoboxSource(("Darfeld",), ("Q1165827",), None),))
    assert [item["qid"] for item in found._sources("Q0", english)] == ["Q1165827"]
    found._native_links = lambda qid: InfoboxLinks((), ())
    written = found._sources("Q0", english)
    assert written[0]["label"] == "Chemayungdung glacier" and written[0]["classes"] == {"glacier"}
