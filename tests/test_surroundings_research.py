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
