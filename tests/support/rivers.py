import contextlib
import dataclasses
import json
import pathlib
import tempfile
from unittest import mock

import geopandas as gpd
import requests

from geography import places
from geography.countrydeck.places import resolve_shapes
from geography.features.catalog import complete_rivers
from geography.features.kinds import Kind
from geography.features.model import Feature
from geography.features.riverart.facts import RiverArt, RiverResearch
from geography.features.spatial import CRS
from geography.sources.http import create_session
from geography.sources.wikidata import Wikidata
from tests.conftest import COUNTRY_DECKS, DATA
from tests.support.cassette import Cassette

RECORDINGS = pathlib.Path(__file__).parents[1] / "fixtures" / "http"
DECKS = {"UP": (DATA, None), "IRN": (COUNTRY_DECKS / "IRN", "fa"), "NLD": (COUNTRY_DECKS / "NLD", "nl")}


@dataclasses.dataclass(frozen=True)
class Researched:
    deck: str
    feature: Feature
    document: dict
    art: RiverArt


@contextlib.contextmanager
def recorded(name: str, record: bool):
    cassette = Cassette(RECORDINGS / f"{name}.json", record)
    with tempfile.TemporaryDirectory() as cache, \
            mock.patch.object(requests.Session, "request", lambda session, method, url, **kwargs: cassette.request(session, method, url, **kwargs)), \
            mock.patch.object(places.Nominatim.__init__, "__defaults__", (pathlib.Path(cache),)), \
            mock.patch.object(places, "REQUEST_INTERVAL", places.REQUEST_INTERVAL if record else 0):
        try:
            yield
        finally:
            cassette.save()


def research(deck: str, slug: str, record: bool) -> Researched:
    root, language = DECKS[deck]
    document = json.loads((root / "places" / "features" / f"{slug}.json").read_text())
    with recorded(f"{deck}_{slug}", record):
        session = create_session()
        wikidata = Wikidata(session)
        feature = Feature(document["key"], document["name"], Kind.RIVER, gpd.GeoSeries([], crs=CRS), qid=document["qid"], wikipedia=document["wikipedia"])
        resolve_shapes(wikidata, [feature])
        complete_rivers(wikidata, [feature], {feature.qid: document["length_km"]})
        art = RiverResearch(session, wikidata, language).art(feature)
    return Researched(deck, feature, document, art)
