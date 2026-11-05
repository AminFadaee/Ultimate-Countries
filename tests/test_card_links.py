import json

import pytest

from geography.countrydeck.deck import (
    CountryDeckBuilder,
    city_templates,
    native_languages,
    neighbour_templates,
    province_templates,
)
from geography.countrydeck.places import with_native_article
from geography.deck import CITY_TEMPLATES, COUNTRY_TEMPLATES, links
from geography.features.deck import TEMPLATES, search_query
from geography.sources.wikidata import Wikidata
from tests.conftest import COUNTRY_DECKS

TEMPLATE_SETS = {
    "country": COUNTRY_TEMPLATES, "city": CITY_TEMPLATES,
    "province": province_templates(), "deck city": city_templates(), "neighbour": neighbour_templates(),
}


@pytest.mark.parametrize("name", TEMPLATE_SETS)
def test_answers_end_with_links_and_questions_have_none(name):
    for template in TEMPLATE_SETS[name]:
        assert template.back.endswith("></div>") and 'class="links"' in template.back
        assert 'class="links"' not in template.front


def test_country_deck_templates_are_the_same_whichever_deck_built_them(tmp_path):
    def templates(code: str) -> list:
        meta = json.loads((COUNTRY_DECKS / code / "meta.json").read_text())
        builder = CountryDeckBuilder(meta, tmp_path / code, tmp_path)
        return [model.templates for model in (builder.province_model, builder.city_model, builder.neighbour_model, builder.place_model)]
    assert templates("IRN") == templates("NLD")


def test_each_country_deck_links_the_wikipedia_in_its_own_language():
    assert native_languages(COUNTRY_DECKS) == {"Iran": "fa", "Netherlands": "nl"}
    assert all('data-country="{{Country}}"' in template.back for template in province_templates())


def test_places_carry_their_links_in_the_details_field():
    assert all('class="links"' not in template.back for template in TEMPLATES)
    assert links(search_query({"name": "Taj Mahal", "countries": ["India"]}), "Q9141") == \
        '<div class="links" data-qid="Q9141" data-query="Taj Mahal, India"></div>'


def test_a_place_without_an_english_article_gets_no_english_wikipedia_link():
    assert 'data-english="no"' in links("Texel, Netherlands", "Q47009672", "nl", english=False)


def test_a_place_in_several_countries_is_searched_by_name_alone():
    assert search_query({"name": "Victoria Falls", "countries": ["Zambia", "Zimbabwe"]}) == "Victoria Falls"


def test_a_place_without_a_wikidata_item_links_no_wikipedia():
    assert 'data-qid=""' in links("Congo Rainforest", None)


def test_a_country_deck_place_without_a_native_article_gets_no_native_link():
    content = with_native_article({"name": "Niavaran Complex", "wikipedia": "https://en.wikipedia.org/wiki/Niavaran_Complex", "fame": 3}, None)
    assert list(content) == ["name", "wikipedia", "native_wikipedia", "fame"] and content["native_wikipedia"] is None
    assert "data-native" not in links("Niavaran Complex, Iran", "Q1984633", "fa" if content["native_wikipedia"] else None)


def test_native_articles_are_read_from_the_wikidata_sitelinks():
    class Answering:
        def get(self, url, timeout, params):
            entities = {"Q1": {"sitelinks": {"fawiki": {"url": "https://fa.wikipedia.org/wiki/x"}}}, "Q2": {"sitelinks": {}}}
            return type("Response", (), {"raise_for_status": lambda self: None, "json": lambda self: {"entities": entities}})()
    assert Wikidata(Answering()).articles({"Q1", "Q2"}, "fawiki") == {"Q1": "https://fa.wikipedia.org/wiki/x"}
