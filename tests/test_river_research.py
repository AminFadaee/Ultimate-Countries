import dataclasses

import geopandas as gpd
import pytest

from geography.features.catalog import drawn_km
from geography.features.riverart.kinds import SourceKind
from geography.features.spatial import CRS


@dataclasses.dataclass(frozen=True)
class Expected:
    deck: str
    slug: str
    source: str | None
    kind: SourceKind | None
    mouth: str

    @property
    def id(self) -> str:
        return f"{self.deck}_{self.slug}"


RIVERS = [
    Expected("UP", "volga", "Valdai Hills", SourceKind.HIGHLAND, "Caspian Sea"),
    Expected("UP", "brahmaputra_river", "Chemayungdung glacier", SourceKind.GLACIER, "Ganges"),
    Expected("UP", "oder", "Oderské vrchy", SourceKind.HIGHLAND, "Szczecin Lagoon"),
    Expected("UP", "orinoco_river", "Parima Mountains", SourceKind.HIGHLAND, "Atlantic Ocean"),
    Expected("UP", "amur_river", "Shilka / Argun River", SourceKind.UNKNOWN, "Sea of Okhotsk"),
    Expected("UP", "congo_river", "Boyoma Falls", None, "Atlantic Ocean"),
    Expected("UP", "danube", None, None, "Black Sea"),
    Expected("UP", "yellow_river", "Bayan Har Mountains", SourceKind.HIGHLAND, "Bohai Sea"),
    Expected("NLD", "amstel", "Aarkanaal", SourceKind.CANAL, "IJ"),
    Expected("NLD", "vechte", "Darfeld", SourceKind.VILLAGE, "Zwarte Water"),
    Expected("NLD", "lek", None, None, "Nieuwe Maas"),
    Expected("IRN", "atrek", "Hezar Masjed Mountains", SourceKind.HIGHLAND, "Caspian Sea"),
    Expected("IRN", "hari", "Koh-e Baba", SourceKind.HIGHLAND, "Karakum Desert"),
    Expected("IRN", "sefid_rud", "Zagros Mountains", SourceKind.HIGHLAND, "Caspian Sea"),
]


@pytest.mark.parametrize("expected", RIVERS, ids=lambda expected: expected.id)
def test_rivers_find_their_source_and_mouth(researched, expected):
    art = researched(expected).art
    assert (art.source.name if art.source else None) == expected.source
    if expected.kind:
        assert art.source.kind is expected.kind
    assert art.mouth.name == expected.mouth


def find(expected_id: str) -> Expected:
    return next(river for river in RIVERS if river.id == expected_id)


def test_a_river_without_a_named_source_starts_at_drawn_source_rivers(researched):
    assert researched(find("UP_danube")).art.source_rivers


def test_a_river_ends_where_it_meets_the_river_it_flows_into(researched):
    lek = researched(find("NLD_lek"))
    assert drawn_km(gpd.GeoSeries([lek.art.path], crs=CRS)) <= 1.3 * lek.document["length_km"]


def test_the_volga_runs_to_the_caspian_and_not_to_the_kama(researched):
    volga = researched(find("UP_volga"))
    assert drawn_km(gpd.GeoSeries([volga.art.path], crs=CRS)) >= 0.8 * volga.document["length_km"]


def test_a_desert_mouth_is_recorded_as_a_desert(researched):
    assert "desert" in researched(find("IRN_hari")).art.mouth.classes


def test_a_source_range_far_larger_than_its_river_is_not_framed(researched):
    assert researched(find("IRN_sefid_rud")).art.source_area is None
