import pytest
from shapely.geometry import Point

from geography.features.kinds import Kind
from tests.support.rivers import surroundings


@pytest.fixture(scope="module")
def found(recording):
    cache = {}

    def get(deck: str, slug: str):
        if (deck, slug) not in cache:
            cache[deck, slug] = surroundings(deck, slug, recording)
        return cache[deck, slug]
    return get


def test_a_lake_shows_its_outflow_first(found):
    rivers = [river.name for river in found("UP", "lake_victoria").rivers]
    assert rivers[0] == "White Nile" and len(rivers) <= 4


def test_the_panama_canal_names_both_oceans_and_the_atlantic_reaches_the_caribbean(found):
    waters = {water.name: water for water in found("UP", "panama_canal").waters}
    assert set(waters) == {"Atlantic Ocean", "Pacific Ocean"}
    assert waters["Atlantic Ocean"].shape.union_all().contains(Point(-79.5, 10.5))


def test_a_gulf_without_an_outline_keeps_its_point(found):
    saronic = next(water for water in found("UP", "corinth_canal").waters if water.name == "Saronic Gulf")
    assert saronic.shape is None and saronic.point is not None


def test_a_waterfall_without_a_linked_river_finds_the_nearest_one(found):
    niagara = found("UP", "niagara_falls")
    assert [river.name for river in niagara.rivers] == ["Niagara"] and niagara.landmark is Kind.WATERFALL


def test_a_canyon_lies_on_its_river(found):
    canyon = found("UP", "grand_canyon")
    assert [river.name for river in canyon.rivers] == ["Colorado River"] and canyon.landmark is Kind.CANYON


def test_a_mountain_sits_in_its_range(found):
    everest = found("UP", "mount_everest")
    assert [outline.name for outline in everest.ranges] == ["Himalayas"] and everest.landmark is Kind.MOUNTAIN


def test_a_range_shows_its_highest_peak(found):
    assert [peak.name for peak in found("UP", "alps").peaks] == ["Mont Blanc"]


def test_a_peninsula_names_the_waters_around_it(found):
    waters = [water.name for water in found("UP", "arabian_peninsula").waters]
    assert {"Red Sea", "Persian Gulf"} <= set(waters) and len(waters) <= 4


def test_an_isthmus_skips_obscure_bays_for_the_waters_it_touches(found):
    waters = [water.name for water in found("UP", "isthmus_of_panama").waters]
    assert "Caribbean Sea" in waters and not any("Mosqu" in name for name in waters)


def test_a_valley_shows_the_river_crossing_it(found):
    assert "Syr Darya" in [river.name for river in found("UP", "ferghana_valley").rivers]


def test_a_landmark_shows_its_river_but_not_its_town(found):
    taj = found("UP", "taj_mahal")
    assert [river.name for river in taj.rivers] == ["Yamuna"] and not taj.towns


def test_a_park_shows_its_town(found):
    assert [town.name for town in found("NLD", "hoge_veluwe_national_park").towns] == ["Ede"]
