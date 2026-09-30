import html
import logging
import pathlib
from dataclasses import dataclass
from datetime import date

import genanki

from geography.deck import (
    CSS,
    REPOSITORY_URL,
    TAG_UNSAFE,
    ULTIMATE_GEOGRAPHY_URL,
    UNKNOWN_ENTITY,
    MediaLibrary,
    MissingMediaError,
    RoutedCard,
    RoutedNote,
    conditional,
    entity,
    placeholder,
    stable_id,
)
from geography.features.kinds import Kind

DECK_NAME = "Ultimate Places"
PLACE_MODEL_ID = 1_730_418_564
TAG_PREFIX = "UP"
MEDIA_PREFIX = "up"
MIN_NOTABLE_HEIGHT_M = 20
FIELDS = ("Id", "Name", "Kind", "Map", "Photo", "Details")
PLACES_CSS = CSS + """
.place-map { position: relative; }
.place-map .photo-badge img { position: absolute; left: 6px; top: 6px; width: 64px; height: 64px !important; max-height: 64px;
  object-fit: cover; border-radius: 50%; border: 3px solid #fff; box-shadow: 0 1px 4px rgba(0, 0, 0, .35); }
.photo img { width: 100%; max-height: 220px; object-fit: cover; border-radius: 6px; }
.photo-front { display: grid; place-items: center; min-height: 260px; }
.photo-front .photo img { max-height: 340px; }
.photo + .map { margin-top: 10px; }
.kind-name { color: var(--kind); font-weight: 600; }
.nightMode .kind-name, .night_mode .kind-name { filter: brightness(1.6); }
"""

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaceTemplate:
    name: str
    requires: str
    front: str
    back: str


def kind_label() -> str:
    return '<hr class="divider"><div class="label">{{Kind}}</div>'


def map_block(with_badge: bool) -> str:
    badge = '{{#Photo}}<span class="photo-badge">{{Photo}}</span>{{/Photo}}' if with_badge else ""
    return f'<div class="value image map place-map">{{{{Map}}}}{badge}</div>'


PHOTO_BLOCK = '<div class="value image photo">{{Photo}}</div>'
DETAILS = '<div class="context">{{Details}}</div>'
ANSWER = entity(placeholder("Name"), answered=True)

TEMPLATES = (
    PlaceTemplate(
        "Map",
        "Map",
        conditional("Map", UNKNOWN_ENTITY + kind_label() + map_block(with_badge=True)),
        ANSWER + kind_label() + map_block(with_badge=True) + DETAILS,
    ),
    PlaceTemplate(
        "Photo",
        "Photo",
        conditional("Photo", f'<div class="photo-front">{PHOTO_BLOCK}</div>'),
        ANSWER + kind_label() + PHOTO_BLOCK + map_block(with_badge=False) + DETAILS,
    ),
)


def long(length_km: float) -> str:
    return f"{length_km:,.0f} km long" if length_km >= 1 else f"{length_km * 1000:,.0f} m long"


def fact(document: dict) -> str | None:
    kind = Kind(document["kind"])
    if kind in (Kind.MOUNTAIN_RANGE, Kind.RIVER, Kind.CANAL, Kind.CANYON) and document["length_km"]:
        return long(document["length_km"])
    if kind in (Kind.MOUNTAIN, Kind.VOLCANO) and document["elevation_m"]:
        return f"{document['elevation_m']:,.0f} m"
    if kind is Kind.WATERFALL and document["height_m"]:
        return f"{document['height_m']:,.0f} m tall"
    if kind is Kind.LANDMARK and (document["height_m"] or 0) >= MIN_NOTABLE_HEIGHT_M:
        return f"{document['height_m']:,.0f} m tall"
    if kind is Kind.LANDMARK and document["length_km"]:
        return long(document["length_km"])
    if kind.spec.shape.is_area and document["area_km2"]:
        area = document["area_km2"]
        return f"{area / 1e6:.1f} million km²" if area >= 1e6 else f"{area:,.0f} km²"
    return None


def location(document: dict) -> str | None:
    countries = document["countries"]
    if document["city"] and countries:
        return f"{document['city']}, {countries[0]}"
    if countries:
        return ("Country: " if len(countries) == 1 else "Countries: ") + ", ".join(countries)
    return None


def details_html(document: dict) -> str:
    kind = Kind(document["kind"])
    colour = kind.spec.palette.outline
    first = f'<span class="kind-name" style="--kind: {colour}">{html.escape(kind.value)}</span>'
    if extra := fact(document):
        first += f" · {html.escape(extra)}"
    lines = [first]
    if place := location(document):
        lines.append(html.escape(place))
    if document["built"]:
        lines.append(html.escape(" · ".join(part for part in (f"Built {document['built']}", document["culture"]) if part)))
    return "<br>".join(lines)


def tags(document: dict) -> list[str]:
    return sorted({"::".join([TAG_PREFIX, TAG_UNSAFE.sub("_", tag).strip("_")]) for tag in document["tags"]})


def description() -> str:
    return f"""
<p><b>{DECK_NAME}</b>: the world's continents, oceans and seas, mountains and volcanoes, deserts, rivers and lakes,
rainforests, regions and famous landmarks, each shown on a map. Places with a recognisable look also have a photo card.</p>
<p>A companion to Ultimate Countries, inspired by <a href="{ULTIMATE_GEOGRAPHY_URL}">Ultimate Geography</a>.</p>
<p>Everything is in one deck. Every note is tagged with its kind and continent (for example <code>UP::Volcano</code> or
<code>UP::Africa</code>), so you can study one kind at a time with a filtered deck: Tools → Create Filtered Deck,
then search for <code>deck:"Ultimate Places" tag:UP::Volcano</code>.</p>
<p>Data comes from Natural Earth (public domain), Marine Regions IHO sea areas (CC BY), RESOLVE Ecoregions (CC BY),
Wikidata (CC0), Wikipedia (CC BY-SA) and OpenStreetMap (ODbL). Photos come from Wikimedia Commons, each under the
licence given on its Commons page.</p>
<p>Source code, data and new releases: <a href="{REPOSITORY_URL}">{REPOSITORY_URL.removeprefix("https://")}</a>.
Built {date.today().isoformat()}.</p>
"""


class PlacesDeckBuilder:
    def __init__(self, data_dir: pathlib.Path, build_dir: pathlib.Path):
        self.media = MediaLibrary(data_dir, build_dir / "media", MEDIA_PREFIX)
        self.deck = genanki.Deck(stable_id(DECK_NAME), DECK_NAME, description())
        self.model = genanki.Model(
            PLACE_MODEL_ID,
            f"{DECK_NAME} place",
            fields=[{"name": field} for field in FIELDS],
            templates=[{"name": template.name, "qfmt": template.front, "afmt": template.back} for template in TEMPLATES],
            css=PLACES_CSS,
            sort_field_index=1,
        )
        self.notes = 0

    def add(self, document: dict) -> None:
        values = {
            "Id": document["key"],
            "Name": html.escape(document["name"]),
            "Kind": html.escape(document["kind"]),
            "Map": self.media.image(document["map"], "map"),
            "Photo": self.media.image(document["photo"]["file"], "photo") if document["photo"] else "",
            "Details": details_html(document),
        }
        cards = [RoutedCard(ord, self.deck.deck_id) for ord, template in enumerate(TEMPLATES) if values[template.requires]]
        if not cards:
            return
        self.deck.add_note(RoutedNote(
            cards,
            model=self.model,
            fields=[values[name] for name in FIELDS],
            tags=tags(document),
            guid=genanki.guid_for(DECK_NAME, document["key"]),
        ))
        self.notes += 1

    def write(self, output: pathlib.Path) -> None:
        if self.media.missing:
            examples = ", ".join(str(path) for path in self.media.missing[:3])
            raise MissingMediaError(f"{len(self.media.missing)} media files are missing, e.g. {examples}")
        output.parent.mkdir(parents=True, exist_ok=True)
        package = genanki.Package([self.deck], media_files=sorted(set(self.media.files)))
        package.write_to_file(str(output))
        logger.info("Wrote %d places and %d media files to %s", self.notes, len(set(self.media.files)), output)


def build_places_deck(documents: list[dict], data_dir: pathlib.Path, build_dir: pathlib.Path, output: pathlib.Path) -> None:
    builder = PlacesDeckBuilder(data_dir, build_dir)
    for document in sorted(documents, key=lambda document: document["name"]):
        builder.add(document)
    builder.write(output)
