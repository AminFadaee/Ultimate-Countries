import json
import logging
import pathlib
import re
from datetime import date

import genanki

from geography.cards import CardType, answers
from geography.countrydeck.collect import COUNTRY_DECKS_DIR, load_documents
from geography.countrydeck.hints import masked
from geography.countrydeck.places import read_documents
from geography.countrydeck.selection import PHOTO_CARD_KINDS
from geography.deck import (
    COUNTRIES_ANKIWEB_URL,
    COUNTRY_NAME,
    CSS,
    FACTS,
    FACTS_CSS,
    MAP_ZOOM_CSS,
    MAP_ZOOM_SCRIPT,
    PLACES_ANKIWEB_URL,
    REPOSITORY_URL,
    ULTIMATE_GEOGRAPHY_URL,
    UNKNOWN_ENTITY,
    MediaLibrary,
    SelectedNote,
    Template,
    ask_attribute,
    chip,
    conditional,
    entity,
    escaped,
    face,
    image,
    stable_id,
)
from geography.features.deck import (
    DETAILS,
    MAP_BLOCK,
    PHOTO_BLOCK,
    PLACES_CSS,
    details_html,
    kind_label,
)
from geography.features.kinds import Kind
from geography.countrydeck.speech import CLIPS, load_manifest
from geography.naming import answer_path, slugify

logger = logging.getLogger(__name__)
PROVINCE_MODEL_ID = 1_730_418_570
NEIGHBOUR_MODEL_ID = 1_730_418_571
CITY_MODEL_ID = 1_730_418_572
PLACE_MODEL_ID = 1_730_418_573
MODEL_FAMILY = "Ultimate Nation"
TAG_UNSAFE = re.compile(r"[^\w-]+")
CENTRE_LINE = '{{#CentreNative}}<div class="subtitle">{{CentreNative}}{{CentreAudio}}</div>{{/CentreNative}}'
NATIVE_LINE = '{{#Native}}<div class="subtitle">{{Native}}{{NativeAudio}}</div>{{/Native}}'
AUDIO_CSS = ".speak { cursor: pointer; margin-left: 10px; font-size: 0.8em; opacity: 0.7; } .speak:hover { opacity: 1; } audio { display: none; }\n"
KIND_CONTEXT = "{{Country}} · {{Kind}}"

PROVINCE_FIELDS = ("Id", "Name", "Native", "NativeAudio", "Centre", "CentreNative", "CentreAudio", "Map", "MapAnswer", "Population", "Area", "Country", "Kind")
CITY_FIELDS = ("Id", "City", "Native", "NativeAudio", "Province", "ProvinceNative", "ProvinceAudio", "Map", "MapAnswer", "Population", "Country", "Kind", "IsCentre")
PLACE_FIELDS = ("Id", "Name", "Native", "NativeAudio", "Kind", "Map", "MapAnswer", "Photo", "Hint", "Details")
MAP_ANSWER_BLOCK = MAP_BLOCK.replace("{{Map}}", "{{MapAnswer}}")
NEIGHBOUR_FIELDS = ("Id", "Name", "Flag", "Map", "MapAnswer", "Capital", "Country", "Kind", *(fact.field for fact in FACTS))

PROVINCE_CHIPS = '<div class="facts">' + chip("Centre", "Centre") + chip("Population", "Population") + chip("Area", "Area") + "</div>"
CITY_CHIPS = '<div class="facts">' + chip("Province", "Province") + chip("Population", "Population") + "</div>"
NEIGHBOUR_CHIPS = '<div class="facts">' + chip("Capital", "Capital") + "".join(chip(fact.field, fact.label) for fact in FACTS) + "</div>"


def province_templates() -> tuple[Template, ...]:
    map_front = face(UNKNOWN_ENTITY, "Province", image("Map", "map"))
    map_back = face(entity("{{Name}}" + NATIVE_LINE, answered=True), "Province", image("MapAnswer", "map")) + (
        f'<div class="context">{KIND_CONTEXT}</div>{PROVINCE_CHIPS}'
    )
    centre = ask_attribute("Centre", "Centre", info_field="CentreNative", subject="{{Name}}" + NATIVE_LINE, context=KIND_CONTEXT, facts=PROVINCE_CHIPS, info_suffix="{{CentreAudio}}")
    reverse = ask_attribute("Province", "Name", info_field="Native", subject="{{Centre}}" + CENTRE_LINE, context=KIND_CONTEXT, facts=PROVINCE_CHIPS, info_suffix="{{NativeAudio}}")
    return (
        Template("Map", "Map", conditional("Map", map_front), map_back),
        Template("Centre", "Centre", conditional("Centre", centre[0]), centre[1]),
        Template("Province of", "Centre", conditional("Centre", reverse[0]), reverse[1]),
    )


def city_templates() -> tuple[Template, ...]:
    subject = "{{City}}" + NATIVE_LINE
    front = face(UNKNOWN_ENTITY, "City", image("Map", "map"))
    back = face(entity(subject, answered=True), "City", image("MapAnswer", "map")) + f'<div class="context">{KIND_CONTEXT}</div>{CITY_CHIPS}'
    province = ask_attribute("Province", "Province", info_field="ProvinceNative", subject=subject, context=KIND_CONTEXT, facts=CITY_CHIPS, info_suffix="{{ProvinceAudio}}")
    return (
        Template("Map", "Map", conditional("Map", front), back),
        Template("Province", "City", f"{{{{^IsCentre}}}}{province[0]}{{{{/IsCentre}}}}", province[1]),
    )


HINT = '{{#Hint}}<div class="hint">{{Hint}}</div>{{/Hint}}'
HINT_CSS = ".hint { font-size: 15px; line-height: 1.45; color: var(--info); margin-top: 12px; padding: 0 10px; }\n"


def place_templates() -> tuple[Template, ...]:
    answer = entity("{{Name}}" + NATIVE_LINE, answered=True)
    return (
        Template("Map", "Map", conditional("Map", UNKNOWN_ENTITY + kind_label() + MAP_BLOCK + HINT), answer + kind_label() + MAP_ANSWER_BLOCK + DETAILS),
        Template("Photo", "Photo", conditional("Photo", f'<div class="photo-front">{PHOTO_BLOCK}</div>'),
                 answer + kind_label() + PHOTO_BLOCK + MAP_ANSWER_BLOCK + DETAILS),
    )


def neighbour_templates() -> tuple[Template, ...]:
    front = face(UNKNOWN_ENTITY, "Neighbour", image("Map", "map"))
    back = face(entity(COUNTRY_NAME, answered=True), "Neighbour", image("MapAnswer", "map")) + f'<div class="context">{KIND_CONTEXT}</div>{NEIGHBOUR_CHIPS}'
    return (Template("Map", "Map", conditional("Map", front), back),)


def tag(prefix: str, *parts: str) -> str:
    return "::".join([prefix, *(TAG_UNSAFE.sub("_", part).strip("_") for part in parts)])


def description(name: str, voiced: bool = False) -> str:
    speech = "<p>Press ▶ next to a native name to hear it, spoken by a synthetic voice (it never plays by itself).</p>\n" if voiced else ""
    return f"""
<p><b>Ultimate {name} Geography</b>: provinces and their centres, neighbours, cities, natural features and landmarks, each on a map.</p>
<p>Part of a family with <a href="{COUNTRIES_ANKIWEB_URL}">Ultimate Countries</a> and <a href="{PLACES_ANKIWEB_URL}">Ultimate Places</a>, inspired by
<a href="{ULTIMATE_GEOGRAPHY_URL}">Ultimate Geography</a>. Names are shown in the country's own language too.</p>
{speech}<p>Data comes from Wikidata (CC0), Wikipedia (CC BY-SA), Natural Earth (public domain), Marine Regions (CC BY) and OpenStreetMap (ODbL).
Photos come from Wikimedia Commons, each under the licence given on its Commons page.</p>
<p>Source code, data and new releases: <a href="{REPOSITORY_URL}">{REPOSITORY_URL.removeprefix("https://")}</a>. Built {date.today().isoformat()}.</p>"""


class CountryDeckBuilder:
    def __init__(self, meta: dict, build_dir: pathlib.Path, data_dir: pathlib.Path, tone_photos: bool = True):
        self.meta = meta
        self.name = f"Ultimate {meta['name']} Geography"
        self.prefix = meta["alpha_2"].upper()
        self.media = MediaLibrary(data_dir, build_dir / "media" / meta["code"].lower(), f"nd-{meta['code'].lower()}", tone_photos)
        css = CSS + FACTS_CSS + MAP_ZOOM_CSS + AUDIO_CSS
        self.voice = load_manifest(COUNTRY_DECKS_DIR / meta["code"])
        self.province_model = self._model(PROVINCE_MODEL_ID, "province", PROVINCE_FIELDS, province_templates(), css)
        self.neighbour_model = self._model(NEIGHBOUR_MODEL_ID, "neighbour", NEIGHBOUR_FIELDS, neighbour_templates(), css)
        self.city_model = self._model(CITY_MODEL_ID, "city", CITY_FIELDS, city_templates(), css)
        self.place_model = self._model(PLACE_MODEL_ID, "place", PLACE_FIELDS, place_templates(), PLACES_CSS + HINT_CSS + FACTS_CSS + MAP_ZOOM_CSS + AUDIO_CSS)
        self.deck = genanki.Deck(stable_id(self.name), self.name, description(meta["name"], bool(self.voice)))
        self.notes = 0

    def spoken(self, native: str | None) -> str:
        return self.media.sound(f"country_decks/{self.meta['code']}/{CLIPS}/{self.voice[native]}") if native in self.voice else ""

    def add_province(self, province: dict) -> None:
        centre = province["centre"]
        stem = f"country_decks/{self.meta['code']}/maps/province__{slugify(province['name'])}"
        values = {
            "Id": province["id"], "Name": escaped(province["name"]), "Native": escaped(province["native"]), "NativeAudio": self.spoken(province["native"]),
            "Centre": escaped(centre["name"] if centre else None), "CentreNative": escaped(centre["native"] if centre else None),
            "CentreAudio": self.spoken(centre["native"] if centre else None),
            "Map": self.media.image(f"{stem}.png", "map"), "MapAnswer": self.media.image(f"{stem}-answer.png", "map"),
            "Population": f"{province['population']:,}" if province["population"] else "",
            "Area": f"{province['area_km2']:,.0f} km²" if province["area_km2"] else "", "Country": escaped(self.meta["name"]), "Kind": "Province",
        }
        named_apart = centre and centre["name"].casefold() != province["name"].casefold()
        ords = [0] + ([1, 2] if named_apart else [])
        self._add(self.province_model, PROVINCE_FIELDS, values, ords, [tag(self.prefix, "Province")], province["id"])

    def add_city(self, city: dict, provinces: dict[str, dict], capitals: set[str]) -> None:
        province = provinces[city["province"]]
        stem = f"country_decks/{self.meta['code']}/maps/city__{slugify(city['name'])}"
        values = {
            "Id": city["id"], "City": escaped(city["name"]), "Native": escaped(city["native"]), "NativeAudio": self.spoken(city["native"]), "Province": escaped(province["name"]),
            "ProvinceNative": escaped(province["native"]), "ProvinceAudio": self.spoken(province["native"]), "Map": self.media.image(f"{stem}.png", "map"),
            "MapAnswer": self.media.image(f"{stem}-answer.png", "map"), "Population": f"{city['population']:,}" if city["population"] else "",
            "Country": escaped(self.meta["name"]), "Kind": "Provincial centre" if city["centre"] else "City", "IsCentre": "yes" if city["centre"] else "",
        }
        ords = [0] if city["centre"] else [0, 1]
        self._add(self.city_model, CITY_FIELDS, values, ords, [tag(self.prefix, "City")] + ([tag(self.prefix, "Centre")] if city["centre"] else []) + ([tag(self.prefix, "Capital")] if city["id"] in capitals else []), city["id"])

    def add_place(self, document: dict, in_places: set[str]) -> None:
        root = f"country_decks/{self.meta['code']}"
        kind = Kind(document["kind"])
        photo_card = bool(document["photo"]) and kind in PHOTO_CARD_KINDS and not document.get("natural_site")
        values = {
            "Id": document["key"], "Name": escaped(document["name"]), "Native": escaped(document["native"]), "NativeAudio": self.spoken(document["native"]), "Kind": escaped(document["kind"]),
            "Map": self.media.image(f"{root}/{document['map']}", "map") if document["map"] else "",
            "MapAnswer": self.media.image(f"{root}/{answer_path(pathlib.Path(document['map']))}", "map") if document["map"] else "",
            "Photo": self.media.image(f"{root}/{document['photo']['file']}", "photo") if photo_card else "",
            "Hint": "" if photo_card else escaped(masked(document)),
            "Details": details_html(document),
        }
        ords = [ord for ord, template in enumerate(place_templates()) if values[template.requires]]
        if not ords:
            return
        tags = [tag(self.prefix, document.get("group") or kind.tag)] + ([tag(self.prefix, "World_Heritage")] if document["world_heritage"] else [])
        tags += [tag(self.prefix, "Disputed")] if document.get("disputed") else []
        tags += [tag(self.prefix, "Also_in_Places")] if document["qid"] in in_places else []
        self._add(self.place_model, PLACE_FIELDS, values, ords, tags, document["key"])

    def add_neighbour(self, neighbour: dict, document: dict) -> None:
        stem = f"country_decks/{self.meta['code']}/maps/neighbour__{slugify(neighbour['name'])}"
        found = answers(document)
        values = {
            "Id": document["id"], "Name": escaped(document["name"]), "Flag": self.media.image(document["flag"], "flag") if document["flag"] else "",
            "Map": self.media.image(f"{stem}.png", "map"), "MapAnswer": self.media.image(f"{stem}-answer.png", "map"),
            "Capital": escaped(found[CardType.CAPITAL].text) if CardType.CAPITAL in found else "", "Country": escaped(self.meta["name"]),
            "Kind": "Land neighbour" if neighbour["kind"] == "land" else "Maritime neighbour",
        }
        for fact in FACTS:
            answer = found.get(fact.card_type)
            values[fact.field] = escaped(answer.text) if answer else ""
        kind = "Land" if neighbour["kind"] == "land" else "Maritime"
        self._add(self.neighbour_model, NEIGHBOUR_FIELDS, values, [0], [tag(self.prefix, "Neighbour", kind)], document["id"])

    def write(self, output: pathlib.Path) -> None:
        if self.media.missing:
            raise FileNotFoundError(f"{len(self.media.missing)} media files are missing, e.g. {self.media.missing[0]}")
        output.parent.mkdir(parents=True, exist_ok=True)
        genanki.Package([self.deck], media_files=sorted(set(self.media.files))).write_to_file(str(output))
        logger.info("Wrote %d notes and %d media files to %s", self.notes, len(set(self.media.files)), output)

    def _add(self, model, fields, values, ords, tags, key: str) -> None:
        self.deck.add_note(SelectedNote(ords, model=model, fields=[values[field] for field in fields], tags=sorted(set(tags)),
                                        guid=genanki.guid_for(self.name, model.name, key)))
        self.notes += 1

    @staticmethod
    def _model(model_id: int, kind: str, fields: tuple[str, ...], templates: tuple[Template, ...], css: str) -> genanki.Model:
        return genanki.Model(model_id, f"{MODEL_FAMILY} {kind}", fields=[{"name": field} for field in fields],
                             templates=[template.as_genanki(MAP_ZOOM_SCRIPT) for template in templates], css=css, sort_field_index=1)


def build_country_deck(code: str, build_dir: pathlib.Path, output: pathlib.Path | None = None, data_dir: pathlib.Path = pathlib.Path("data"), tone_photos: bool = True) -> None:
    directory = COUNTRY_DECKS_DIR / code
    meta = json.loads((directory / "meta.json").read_text())
    documents = load_documents(data_dir / "countries")
    builder = CountryDeckBuilder(meta, build_dir, data_dir, tone_photos)
    provinces = json.loads((directory / "provinces.json").read_text())
    for province in provinces:
        builder.add_province(province)
    by_id = {province["id"]: province for province in provinces}
    capitals = {city["id"] for city in documents[meta["code"]]["cities"] if city["role"] == "capital"}
    for city in json.loads((directory / "cities.json").read_text()):
        builder.add_city(city, by_id, capitals)
    for neighbour in json.loads((directory / "neighbours.json").read_text()):
        builder.add_neighbour(neighbour, documents[neighbour["alpha_3"]])
    in_places = {document["qid"] for document in read_documents(data_dir)}
    for document in read_documents(directory):
        builder.add_place(document, in_places)
    builder.write(output or build_dir / f"ultimate_{meta['name'].lower().replace(' ', '_')}_geography.apkg")
