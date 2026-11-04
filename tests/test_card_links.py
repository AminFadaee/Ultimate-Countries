import pytest

from geography.countrydeck.deck import (
    city_templates,
    neighbour_templates,
    province_templates,
)
from geography.deck import CITY_TEMPLATES, COUNTRY_TEMPLATES, links
from geography.features.deck import TEMPLATES, search_query

TEMPLATE_SETS = {
    "country": COUNTRY_TEMPLATES, "city": CITY_TEMPLATES,
    "province": province_templates("nl"), "deck city": city_templates("nl"), "neighbour": neighbour_templates("nl"),
}


@pytest.mark.parametrize("name", TEMPLATE_SETS)
def test_answers_end_with_links_and_questions_have_none(name):
    for template in TEMPLATE_SETS[name]:
        assert template.back.endswith("></div>") and 'class="links"' in template.back
        assert 'class="links"' not in template.front


def test_country_deck_answers_link_the_native_wikipedia():
    assert all('data-native="nl"' in template.back for template in province_templates("nl"))


def test_places_carry_their_links_in_the_details_field():
    assert all('class="links"' not in template.back for template in TEMPLATES)
    assert links(search_query({"name": "Taj Mahal", "countries": ["India"]}), "Q9141") == \
        '<div class="links" data-qid="Q9141" data-query="Taj Mahal, India"></div>'


def test_a_place_in_several_countries_is_searched_by_name_alone():
    assert search_query({"name": "Victoria Falls", "countries": ["Zambia", "Zimbabwe"]}) == "Victoria Falls"


def test_a_place_without_a_wikidata_item_links_no_wikipedia():
    assert 'data-qid=""' in links("Congo Rainforest", None)

