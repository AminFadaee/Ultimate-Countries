import hashlib
import html
import logging
import pathlib
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import genanki
from PIL import Image

from geography.cards import Answer, CardType, answers, shared_context
from geography.naming import slugify

DECK_NAME = "Ultimate Countries"
COUNTRY_MODEL_ID = 1_730_418_562
CITY_MODEL_ID = 1_730_418_563
TAG_PREFIX = "UC"
MEDIA_PREFIX = "uc"
MAP_WIDTH = 1000
MAP_COLORS = 64
ULTIMATE_GEOGRAPHY_URL = "https://github.com/anki-geo/ultimate-geography"
TAG_UNSAFE = re.compile(r"[^\w-]+")
COUNTRY_CONTEXT = "{{Region}}{{#NotableCities}}<br>{{NotableCities}}{{/NotableCities}}"
CITY_CONTEXT = "{{Region}}"

logger = logging.getLogger(__name__)


class Subdeck(StrEnum):
    CAPITALS = "Capitals"
    FLAGS = "Flags"
    MAPS = "Maps"
    DEMONYMS = "Demonyms"
    LANGUAGES = "Languages"
    CURRENCIES = "Currencies"
    RELIGIONS = "Religions"
    POPULATION = "Population"
    GOVERNMENT = "Government"
    CITIES = "Cities"

    @property
    def full_name(self) -> str:
        return f"{DECK_NAME}::{self.value}"

    @property
    def deck_id(self) -> int:
        return stable_id(self.full_name)


@dataclass(frozen=True)
class Template:
    name: str
    subdeck: Subdeck
    requires: str
    front: str
    back: str

    def as_genanki(self) -> dict:
        return {"name": self.name, "qfmt": self.front, "afmt": self.back}


@dataclass(frozen=True)
class TextCard:
    card_type: CardType
    subdeck: Subdeck
    field: str


class RoutedCard(genanki.Card):
    def __init__(self, ord: int, deck_id: int):
        super().__init__(ord)
        self.deck_id = deck_id

    def write_to_db(self, cursor, timestamp: float, deck_id, note_id, id_gen, due=0):
        super().write_to_db(cursor, timestamp, self.deck_id, note_id, id_gen, due)


class RoutedNote(genanki.Note):
    def __init__(self, routed_cards: list[RoutedCard], **kwargs):
        super().__init__(**kwargs)
        self.routed_cards = routed_cards

    @property
    def cards(self) -> list[RoutedCard]:
        return self.routed_cards


def stable_id(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest()[:12], 16) % (1 << 40) + (1 << 30)


def prompt(kind: str, subject: str) -> str:
    return f'<div class="prompt"><span class="kind">{kind}:</span> {subject}</div>'


def image_prompt(kind: str, field: str) -> str:
    return f'<div class="kind">{kind}</div><div class="image">{{{{{field}}}}}</div>'


def back(answer: str, info_field: str | None = None, context: str = COUNTRY_CONTEXT) -> str:
    info = f'{{{{#{info_field}}}}}<div class="info">{{{{{info_field}}}}}</div>{{{{/{info_field}}}}}' if info_field else ""
    return f'{{{{FrontSide}}}}<hr id="answer"><div class="answer">{answer}</div>{info}<div class="context">{context}</div>'


def conditional(field: str, content: str) -> str:
    return f"{{{{#{field}}}}}{content}{{{{/{field}}}}}"


TEXT_CARDS = (
    TextCard(CardType.DEMONYM, Subdeck.DEMONYMS, "Demonym"),
    TextCard(CardType.LANGUAGE, Subdeck.LANGUAGES, "Language"),
    TextCard(CardType.CURRENCY, Subdeck.CURRENCIES, "Currency"),
    TextCard(CardType.RELIGION, Subdeck.RELIGIONS, "Religion"),
    TextCard(CardType.POPULATION, Subdeck.POPULATION, "Population"),
    TextCard(CardType.GOVERNMENT, Subdeck.GOVERNMENT, "Government"),
)

COUNTRY_TEMPLATES = (
    Template(
        "Capital",
        Subdeck.CAPITALS,
        "Capital",
        conditional("Capital", prompt("Capital", "{{Name}}")),
        back("{{Capital}}", "CapitalInfo"),
    ),
    Template(
        "Capital of",
        Subdeck.CAPITALS,
        "Capital",
        conditional("Capital", prompt("Capital of", "{{Capital}}")),
        back("{{Name}}", "CapitalInfo"),
    ),
    Template("Flag", Subdeck.FLAGS, "Flag", conditional("Flag", image_prompt("Flag", "Flag")), back("{{Name}}")),
    Template("Map", Subdeck.MAPS, "Map", conditional("Map", image_prompt("Map", "Map")), back("{{Name}}")),
    *(
        Template(
            card.field,
            card.subdeck,
            card.field,
            conditional(card.field, prompt(card.field, "{{Name}}")),
            back(f"{{{{{card.field}}}}}", f"{card.field}Info"),
        )
        for card in TEXT_CARDS
    ),
)

COUNTRY_FIELDS = (
    "Id",
    "Name",
    "Capital",
    "CapitalInfo",
    "Flag",
    "Map",
    *(name for card in TEXT_CARDS for name in (card.field, f"{card.field}Info")),
    "Region",
    "NotableCities",
)

CITY_TEMPLATES = (
    Template(
        "Map",
        Subdeck.CITIES,
        "Map",
        conditional("Map", image_prompt("City", "Map")),
        back("{{City}}<br>{{Country}}", context=CITY_CONTEXT),
    ),
    Template(
        "Country",
        Subdeck.CITIES,
        "City",
        f"{{{{^IsCapital}}}}{prompt('Country', '{{City}}')}{{{{/IsCapital}}}}",
        back("{{Country}}", context=CITY_CONTEXT),
    ),
)

CITY_FIELDS = ("Id", "City", "Country", "Map", "IsCapital", "Region")

CSS = """
.card { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; font-size: 22px; text-align: center; color: #1d1d1f; background: #fdfdfd; }
.nightMode.card, .night_mode .card { color: #e8e8ea; background: #1e1e20; }
.prompt { font-size: 26px; }
.kind { color: #8a8a8e; font-size: 18px; letter-spacing: 0.02em; }
.image img { max-width: 100%; max-height: 70vh; margin-top: 8px; }
.answer { font-size: 28px; font-weight: 600; margin: 8px 0; }
.info { color: #555; font-size: 17px; margin: 6px auto; max-width: 42em; }
.nightMode .info, .night_mode .info { color: #b5b5ba; }
.context { color: #8a8a8e; font-size: 15px; margin-top: 14px; }
"""


def description() -> str:
    return f"""
<p><b>{DECK_NAME}</b>: countries, territories and their major cities.</p>
<p>Inspired by <a href="{ULTIMATE_GEOGRAPHY_URL}">Ultimate Geography</a>, whose list of countries and territories it follows,
extended with demonyms, languages, currencies, religions, population and government.</p>
<p>Each question type lives in its own subdeck. To skip a type, <b>suspend</b> its subdeck rather than deleting it,
because deleted cards come back when you import an update.</p>
<p>Data is collected automatically from Wikidata (CC0), Wikipedia (CC BY-SA), restcountries, the World Bank,
the Pew Research Center, Unicode CLDR, GeoNames (CC BY), ISO 4217 and Natural Earth (public domain);
maps use OpenStreetMap data (ODbL).</p>
<p>Built {date.today().isoformat()}.</p>
"""


def tag(*parts: str) -> str:
    return "::".join([TAG_PREFIX, *(TAG_UNSAFE.sub("_", part).strip("_") for part in parts)])


def region_tags(document: dict) -> list[str]:
    return [tag(part) for part in (document["region"], document["subregion"]) if part]


def country_tags(document: dict) -> list[str]:
    status = document["status"]
    tags = region_tags(document)
    tags.append(tag("Sovereign" if status["sovereign"] else "Territory"))
    if status["disputed"]:
        tags.append(tag("Disputed"))
    tags.extend(tag(organization) for organization in document["memberships"])
    tags.extend(tag(currency["name"]) for currency in document["currencies"] if currency["main"])
    return sorted(set(tags))


def escaped(value: str | None) -> str:
    return html.escape(value or "")


def info(answer: Answer | None) -> str:
    return "<br>".join(escaped(line) for line in answer.context) if answer else ""


class MediaLibrary:
    def __init__(self, data_dir: pathlib.Path, build_dir: pathlib.Path):
        self.data_dir = data_dir
        self.build_dir = build_dir
        self.files: list[str] = []

    def image(self, relative: str | None, kind: str) -> str:
        if not relative:
            return ""
        source = self.data_dir / relative
        if not source.exists():
            logger.warning("Missing media %s", source)
            return ""
        target = self.build_dir / f"{MEDIA_PREFIX}-{kind}-{slugify(source.stem)}{source.suffix}"
        if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
            self._prepare(source, target)
        self.files.append(str(target))
        return f'<img src="{target.name}">'

    def _prepare(self, source: pathlib.Path, target: pathlib.Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix != ".png":
            target.write_bytes(source.read_bytes())
            return
        with Image.open(source) as image:
            height = round(image.height * MAP_WIDTH / image.width)
            small = image.convert("RGB").resize((MAP_WIDTH, height), Image.LANCZOS)
            small.quantize(colors=MAP_COLORS, method=Image.Quantize.MEDIANCUT).save(target, optimize=True)


class DeckBuilder:
    def __init__(self, data_dir: pathlib.Path, build_dir: pathlib.Path):
        self.media = MediaLibrary(data_dir, build_dir / "media")
        self.country_model = self._model(COUNTRY_MODEL_ID, f"{DECK_NAME} country", COUNTRY_FIELDS, COUNTRY_TEMPLATES)
        self.city_model = self._model(CITY_MODEL_ID, f"{DECK_NAME} city", CITY_FIELDS, CITY_TEMPLATES)
        self.root = genanki.Deck(stable_id(DECK_NAME), DECK_NAME, description())
        self.subdecks = [genanki.Deck(subdeck.deck_id, subdeck.full_name) for subdeck in Subdeck]
        self.notes = 0

    def add_country(self, document: dict) -> None:
        found = answers(document)
        context = shared_context(document)
        values = {
            "Id": document["id"],
            "Name": escaped(document["name"]),
            "Capital": escaped(found[CardType.CAPITAL].text) if CardType.CAPITAL in found else "",
            "CapitalInfo": info(found.get(CardType.CAPITAL)),
            "Flag": self.media.image(document["flag"], "flag") if CardType.FLAG in found else "",
            "Map": self.media.image(document["map"], "map") if CardType.MAP in found else "",
            "Region": escaped(context[0]) if context else "",
            "NotableCities": escaped(context[1]) if len(context) > 1 else "",
        }
        for card in TEXT_CARDS:
            answer = found.get(card.card_type)
            values[card.field] = escaped(answer.text) if answer else ""
            values[f"{card.field}Info"] = info(answer)

        cards = [
            RoutedCard(ord, template.subdeck.deck_id)
            for ord, template in enumerate(COUNTRY_TEMPLATES)
            if values[template.requires]
        ]
        self._add(self.country_model, COUNTRY_FIELDS, values, cards, country_tags(document), document["id"])
        for city in document["cities"]:
            self.add_city(city, document)

    def add_city(self, city: dict, country: dict) -> None:
        is_capital = city["role"] == "capital"
        region = " · ".join(part for part in (country["region"], country["subregion"]) if part)
        values = {
            "Id": city["id"],
            "City": escaped(city["name"]),
            "Country": escaped(country["name"]),
            "Map": self.media.image(city["map"], "city"),
            "IsCapital": "yes" if is_capital else "",
            "Region": escaped(region),
        }
        cards = []
        if values["Map"]:
            cards.append(RoutedCard(0, Subdeck.CITIES.deck_id))
        if not is_capital:
            cards.append(RoutedCard(1, Subdeck.CITIES.deck_id))
        tags = [tag("City"), *region_tags(country)] + ([tag("Capital")] if is_capital else [])
        self._add(self.city_model, CITY_FIELDS, values, cards, tags, city["id"], country["id"])

    def write(self, output: pathlib.Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        package = genanki.Package([self.root, *self.subdecks], media_files=sorted(set(self.media.files)))
        package.write_to_file(str(output))
        logger.info("Wrote %d notes and %d media files to %s", self.notes, len(set(self.media.files)), output)

    def _add(self, model, fields, values, cards, tags, *identity: str) -> None:
        if not cards:
            return
        note = RoutedNote(
            cards,
            model=model,
            fields=[values[name] for name in fields],
            tags=sorted(set(tags)),
            guid=genanki.guid_for(*identity),
        )
        self.root.add_note(note)
        self.notes += 1

    @staticmethod
    def _model(model_id: int, name: str, fields: tuple[str, ...], templates: tuple[Template, ...]) -> genanki.Model:
        return genanki.Model(
            model_id,
            name,
            fields=[{"name": field} for field in fields],
            templates=[template.as_genanki() for template in templates],
            css=CSS,
            sort_field_index=1,
        )


def build_deck(documents: list[dict], data_dir: pathlib.Path, build_dir: pathlib.Path, output: pathlib.Path) -> None:
    builder = DeckBuilder(data_dir, build_dir)
    for document in sorted(documents, key=lambda document: document["name"]):
        builder.add_country(document)
    builder.write(output)
