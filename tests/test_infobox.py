import pytest

from geography.features.riverart.infobox import InfoboxLinks, RiverInfobox, coordinates

ITEMS = {
    "Manasarovar": "Q233627",
    "Himalayas": "Q5451",
    "Oderské vrchy": "Q1636366",
    "Parima Mountains": "Q1053290",
    "Venezuela": "Q717",
    "Guaviare River": "Q1047596",
    "Darfeld": "Q1165827",
    "Westphalia": "Q8614",
    "Onon (river)": "Q206694",
    "Shilka (river)": "Q115456",
    "Caspian Sea": "Q5484",
}


class SavedInfobox(RiverInfobox):
    def __init__(self, wikitext: str):
        self.wikitext = wikitext

    def _get(self, parameters: dict) -> dict:
        return {"parse": {"wikitext": self.wikitext}}

    def _item(self, title: str) -> str | None:
        return ITEMS.get(title)


def read(wikitext: str) -> InfoboxLinks:
    return SavedInfobox(wikitext).links("River")


@pytest.mark.parametrize(("text", "expected"), [
    ("{{coord|37|10|N|59|00|E|display=inline, title}}", (59.0, 37.1667)),
    ("{{coord|52|12|24|N|4|44|5|E}}", (4.7347, 52.2067)),
    ("{{Coord|52.36|4.9|display=inline}}", (4.9, 52.36)),
    ("{{coord|33|30|S|70|40|W}}", (-70.6667, -33.5)),
    ("no coordinates here", None),
])
def test_coordinates_in_every_degree_form(text, expected):
    found = coordinates(text)
    assert found == expected if expected is None else found == pytest.approx(expected, abs=1e-4)


def test_fields_with_leading_spaces_are_read():
    links = read(" | source1 = [[Manasarovar]]\n | source1_coordinates = {{coord|30|19|N|82|08|E}}\n | mouth = [[Caspian Sea]]")
    assert [source.items for source in links.sources] == [("Q233627",)]
    assert links.mouth == ("Q5484",)


def test_a_written_name_before_a_linked_place_is_the_source():
    links = read("| source1 = Chemayungdung glacier, [[Manasarovar]]\n| source1_coordinates = {{coord|30|19|N|82|08|E}}\n| source1_location = [[Himalayas]]")
    written, place = links.sources[0], links.sources[1]
    assert written.names == ("Chemayungdung glacier",) and written.items == () and not written.located
    assert written.point == pytest.approx((82.1333, 30.3167), abs=1e-4)
    assert place.items == ("Q233627",) and place.located


def test_a_location_field_keeps_its_link_even_after_a_name():
    links = read("| source1 = \n| source1_location = Fidlův kopec, [[Oderské vrchy]]")
    assert links.sources[0].items == ("Q1636366",) and links.sources[0].located


def test_an_unlinked_source_falls_back_to_its_own_location():
    links = read(
        "| source1 = Hydrological source (main stem)\n| source1_location = Cerro Delgado-Chalbaud, [[Parima Mountains]], [[Venezuela]]\n"
        "| source2 = Geographical source (Orinoco–[[Guaviare River|Guaviare]])\n"
    )
    assert links.sources[0].items == ("Q1053290", "Q717") and links.sources[0].located
    assert links.sources[1].items == ("Q1047596",) and not links.sources[1].located


def test_dutch_oorsprong_is_a_source_field():
    links = read("| oorsprong = [[Darfeld]]")
    assert links.sources[0].items == ("Q1165827",) and not links.sources[0].located


def test_red_links_keep_their_names():
    links = read("| source1 = [[Aarkanaal]] / [[Drecht]]\n| source1_coordinates = {{coord|52|12|24|N|4|44|5|E}}")
    assert links.sources[0].names == ("Aarkanaal", "Drecht") and links.sources[0].items == ()


def test_linked_rivers_keep_their_labels():
    links = read("| source1 = [[Onon (river)|Onon]]-[[Shilka (river)|Shilka]]")
    assert links.sources[0].names == ("Onon", "Shilka") and links.sources[0].items == ("Q206694", "Q115456")
