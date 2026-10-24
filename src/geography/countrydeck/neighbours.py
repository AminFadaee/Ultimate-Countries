import geopandas as gpd
import requests

from geography.data import NaturalEarth
from geography.render import EQUAL_AREA, get_mainland, get_nearby_parts
from geography.sources.http import TIMEOUT
from geography.sources.marine_regions import WFS_URL

LAND_GAP_M = 1_000
SEA_REACH_M = 300_000
BOUNDARIES_LAYER = "MarineRegions:eez_boundaries"


def normalized(name: str) -> str:
    return name.casefold().removeprefix("the ")


def maritime_lines(session: requests.Session, sovereign: str) -> gpd.GeoDataFrame:
    response = session.get(
        WFS_URL,
        params={"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": BOUNDARIES_LAYER, "outputFormat": "application/json",
                "cql_filter": f"sovereign1='{sovereign}' OR sovereign2='{sovereign}'"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return gpd.GeoDataFrame.from_features(response.json()["features"], crs="EPSG:4326")


def neighbours_of(session: requests.Session, data: NaturalEarth, code: str, documents: dict[str, dict]) -> list[dict]:
    host = data.country(code).core.geometry
    territory = get_nearby_parts(host, get_mainland(host)).to_crs(EQUAL_AREA).union_all()
    by_name = {normalized(document["name"]): document for document in documents.values()}
    found: dict[str, dict] = {}

    countries = data.countries[data.countries["ADM0_A3"].ne(code)].to_crs(EQUAL_AREA)
    for alpha_3 in countries[countries.geometry.distance(territory) <= LAND_GAP_M]["ADM0_A3"].unique():
        if document := documents.get(alpha_3):
            found[alpha_3] = {"id": document["id"], "alpha_3": alpha_3, "name": document["name"], "kind": "land"}

    sovereign = documents[code]["name"]
    lines = maritime_lines(session, sovereign)
    lines = lines[lines.to_crs(EQUAL_AREA).geometry.distance(territory) <= SEA_REACH_M]
    for row in lines.itertuples():
        other = row.sovereign2 if row.sovereign1 == sovereign else row.sovereign1
        document = by_name.get(normalized(other)) if isinstance(other, str) else None
        if document and document["codes"]["alpha_3"] not in found:
            found[document["codes"]["alpha_3"]] = {"id": document["id"], "alpha_3": document["codes"]["alpha_3"], "name": document["name"], "kind": "sea"}
    return sorted(found.values(), key=lambda item: (item["kind"] != "land", item["name"]))
