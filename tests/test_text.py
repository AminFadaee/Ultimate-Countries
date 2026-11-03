import pytest

from geography.countrydeck.names import normalised
from geography.features.collect import CONTESTED_ALIAS
from geography.features.lengths import disagrees, stated_length_km

ZWNJ = "‌"


def test_persian_half_spaces_survive_normalisation():
    assert normalised(f"فرودگاه بین{ZWNJ}المللی امام خمینی") == f"فرودگاه بین{ZWNJ}المللی امام خمینی"


def test_compatibility_ligatures_read_plainly():
    assert normalised("Stadion Feĳenoord") == "Stadion Feijenoord"


@pytest.mark.parametrize("sentence", [
    "The Persian Gulf, sometimes called the Arabian Gulf, is a sea in West Asia.",
    "The Persian Gulf, also known as Arabian Gulf, is a sea in West Asia.",
])
def test_the_contested_alias_is_removed_from_descriptions(sentence):
    assert "Arabian" not in CONTESTED_ALIAS.sub("", sentence)


@pytest.mark.parametrize(("text", "km"), [
    ("The Danube flows for 2,850 km through ten countries.", 2850),
    ("It is a 1,600-kilometre-long strip of land.", 1600),
    ("The river is about 300 miles long.", 300 * 1.609344),
    ("A river in Germany.", None),
])
def test_stated_lengths_are_read_from_descriptions(text, km):
    found = stated_length_km(text)
    assert found == km if km is None else found == pytest.approx(km)


def test_a_length_that_contradicts_its_description_disagrees():
    assert disagrees(1000, "The river flows for 2,850 km.")
    assert not disagrees(2850, "The river flows for 2,850 km.")
