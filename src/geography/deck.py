import hashlib
import html
import logging
import pathlib
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import genanki

from geography.cards import Answer, CardType, Context, answers, shared_context
from geography.naming import slugify

DECK_NAME = "Ultimate Countries"
COUNTRY_MODEL_ID = 1_730_418_562
CITY_MODEL_ID = 1_730_418_563
TAG_PREFIX = "UC"
MEDIA_PREFIX = "uc"
ULTIMATE_GEOGRAPHY_URL = "https://github.com/anki-geo/ultimate-geography"
REPOSITORY_URL = "https://github.com/AminFadaee/Ultimate-Countries"
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


UNKNOWN_ENTITY = '<div class="entity unknown">?</div>'
UNKNOWN_VALUE = '<div class="value unknown">?</div>'
FLAG_HEIGHT_PX = 160
COUNTRY_NAME = '{{#Flag}}<span class="mini-flag">{{Flag}}</span>{{/Flag}}{{Name}}'
MAP_THUMBNAIL = '{{#Map}}<div class="thumbnail map">{{Map}}</div>{{/Map}}'


def conditional(field: str, content: str) -> str:
    return f"{{{{#{field}}}}}{content}{{{{/{field}}}}}"


def placeholder(field: str) -> str:
    return f"{{{{{field}}}}}"


def entity(content: str, answered: bool = False) -> str:
    return f'<div class="entity answer" id="answer">{content}</div>' if answered else f'<div class="entity">{content}</div>'


def value(content: str, answered: bool = False) -> str:
    return f'<div class="value answer" id="answer">{content}</div>' if answered else f'<div class="value">{content}</div>'


def image(field: str, kind: str) -> str:
    return f'<div class="value image {kind}">{placeholder(field)}</div>'


def face(entity_html: str, label: str, value_html: str) -> str:
    return f'{entity_html}<hr class="divider"><div class="label">{label}</div>{value_html}'


def details(info_field: str | None, context: str, extra: str = "") -> str:
    info = conditional(info_field, f'<div class="info">{placeholder(info_field)}</div>') if info_field else ""
    return f'{info}{extra}<div class="context">{context}</div>'


def ask_attribute(
    label: str, field: str, info_field: str | None = None, subject: str = COUNTRY_NAME, context: str = COUNTRY_CONTEXT
) -> tuple[str, str]:
    front = face(entity(subject), label, UNKNOWN_VALUE)
    back = face(entity(subject), label, value(placeholder(field), answered=True)) + details(info_field, context)
    return front, back


def ask_entity(
    label: str,
    shown: str,
    answer: str = COUNTRY_NAME,
    info_field: str | None = None,
    context: str = COUNTRY_CONTEXT,
    extra: str = "",
) -> tuple[str, str]:
    front = face(UNKNOWN_ENTITY, label, shown)
    back = face(entity(answer, answered=True), label, shown) + details(info_field, context, extra)
    return front, back


def guarded(field: str, faces: tuple[str, str]) -> tuple[str, str]:
    front, back = faces
    return conditional(field, front), back


TEXT_CARDS = (
    TextCard(CardType.DEMONYM, Subdeck.DEMONYMS, "Demonym"),
    TextCard(CardType.LANGUAGE, Subdeck.LANGUAGES, "Language"),
    TextCard(CardType.CURRENCY, Subdeck.CURRENCIES, "Currency"),
    TextCard(CardType.RELIGION, Subdeck.RELIGIONS, "Religion"),
    TextCard(CardType.POPULATION, Subdeck.POPULATION, "Population"),
    TextCard(CardType.GOVERNMENT, Subdeck.GOVERNMENT, "Government"),
)

COUNTRY_TEMPLATES = (
    Template("Capital", Subdeck.CAPITALS, "Capital", *guarded("Capital", ask_attribute("Capital", "Capital", "CapitalInfo"))),
    Template(
        "Capital of",
        Subdeck.CAPITALS,
        "Capital",
        *guarded("Capital", ask_entity("Capital", value(placeholder("Capital")), info_field="CapitalInfo")),
    ),
    Template(
        "Flag",
        Subdeck.FLAGS,
        "Flag",
        *guarded("Flag", ask_entity("Flag", image("Flag", "flag"), answer="{{Name}}", extra=MAP_THUMBNAIL)),
    ),
    Template("Map", Subdeck.MAPS, "Map", *guarded("Map", ask_entity("Map", image("Map", "map")))),
    *(
        Template(
            card.field,
            card.subdeck,
            card.field,
            *guarded(card.field, ask_attribute(card.field, card.field, f"{card.field}Info")),
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

CITY_MAP_FRONT, CITY_MAP_BACK = ask_entity(
    "Map", image("Map", "map"), answer='{{City}}<div class="subtitle">{{Country}}</div>', context=CITY_CONTEXT
)
CITY_COUNTRY_FRONT, CITY_COUNTRY_BACK = ask_attribute("Country", "Country", subject="{{City}}", context=CITY_CONTEXT)

CITY_TEMPLATES = (
    Template("Map", Subdeck.CITIES, "Map", conditional("Map", CITY_MAP_FRONT), CITY_MAP_BACK),
    Template("Country", Subdeck.CITIES, "City", f"{{{{^IsCapital}}}}{CITY_COUNTRY_FRONT}{{{{/IsCapital}}}}", CITY_COUNTRY_BACK),
)

CITY_FIELDS = ("Id", "City", "Country", "Map", "IsCapital", "Region")

CSS = f"""
.card {{
  --text: #1d1d1f; --muted: #8a8a8e; --faint: #c2c2c8; --accent: #0a66c2;
  --line: #d8d8dd; --info: #505055; --outline: rgba(120, 120, 128, 0.35); --background: #fdfdfd;
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif; text-align: center;
  color: var(--text); background: var(--background); padding: 12px 8px;
}}
.nightMode.card, .night_mode .card, .card.night_mode {{
  --text: #e8e8ea; --muted: #9a9aa0; --faint: #5c5c62; --accent: #6cb2ff;
  --line: #3a3a3f; --info: #bdbdc2; --background: #1e1e20;
}}
.entity {{ font-size: 30px; font-weight: 600; }}
.subtitle {{ font-size: 20px; font-weight: 400; color: var(--muted); margin-top: 2px; }}
.divider {{ border: none; border-top: 1px solid var(--line); width: min(60%, 360px); margin: 14px auto; }}
.label {{ font-size: 14px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }}
.value {{ font-size: 26px; }}
.unknown {{ color: var(--faint); font-weight: 600; }}
.answer {{ color: var(--accent); font-weight: 700; }}
.flag img {{ height: {FLAG_HEIGHT_PX}px; width: auto; max-width: 100%; object-fit: contain; filter: drop-shadow(0 0 1px var(--outline)); }}
.map img {{ width: 100%; max-width: 900px; height: auto; max-height: 60vh; object-fit: contain; }}
.mini-flag img {{ height: 0.8em; width: auto; vertical-align: 0.02em; margin-right: 0.4em; filter: drop-shadow(0 0 1px var(--outline)); }}
.thumbnail {{ margin-top: 14px; }}
.thumbnail.map img {{ width: 100%; max-width: 360px; height: auto; }}
.info {{ font-size: 16px; line-height: 1.45; color: var(--info); margin: 10px auto 0; max-width: 40em; }}
.context {{ font-size: 14px; line-height: 1.5; color: var(--muted); margin-top: 22px; }}
"""


def description() -> str:
    return f"""
<p><b>{DECK_NAME}</b>: countries, territories and their major cities.</p>
<p>Inspired by <a href="{ULTIMATE_GEOGRAPHY_URL}">Ultimate Geography</a>, the deck that got me excited about learning geography.
Ultimate Countries covers every country and territory with a permanent population, with demonyms, languages, currencies,
religions, population, government and city maps on top of flags, maps and capitals.</p>
<p>Each question type lives in its own subdeck. To skip a type, <b>suspend</b> its subdeck rather than deleting it,
because deleted cards come back when you import an update.</p>
<p>Data is collected automatically from Wikidata (CC0), Wikipedia (CC BY-SA), restcountries, the World Bank,
the Pew Research Center, Unicode CLDR, GeoNames (CC BY), ISO 3166, ISO 4217 and Natural Earth (public domain);
maps use OpenStreetMap data (ODbL).</p>
<p>Source code, data and new releases: <a href="{REPOSITORY_URL}">{REPOSITORY_URL.removeprefix("https://")}</a>.
Built {date.today().isoformat()}.</p>
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


def context_html(context: Context) -> str:
    return "<br>".join(escaped(line) for line in (context.region, context.status) if line)


def info(answer: Answer | None) -> str:
    return "<br>".join(escaped(line) for line in answer.context) if answer else ""


class MissingMediaError(FileNotFoundError):
    pass


class MediaLibrary:
    def __init__(self, data_dir: pathlib.Path, build_dir: pathlib.Path, prefix: str = MEDIA_PREFIX):
        self.data_dir = data_dir
        self.build_dir = build_dir
        self.prefix = prefix
        self.files: list[str] = []
        self.missing: list[pathlib.Path] = []

    def image(self, relative: str | None, kind: str) -> str:
        if not relative:
            return ""
        source = self.data_dir / relative
        if not source.exists():
            self.missing.append(source)
            return ""
        target = self.build_dir / f"{self.prefix}-{kind}-{slugify(source.stem)}{source.suffix}"
        if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
            self._prepare(source, target)
        self.files.append(str(target))
        return f'<img src="{target.name}">'

    def _prepare(self, source: pathlib.Path, target: pathlib.Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


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
            "Region": context_html(context),
            "NotableCities": escaped(context.notable_cities),
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
        values = {
            "Id": city["id"],
            "City": escaped(city["name"]),
            "Country": escaped(country["name"]),
            "Map": self.media.image(city["map"], "city"),
            "IsCapital": "yes" if is_capital else "",
            "Region": context_html(shared_context(country)),
        }
        cards = []
        if values["Map"]:
            cards.append(RoutedCard(0, Subdeck.CITIES.deck_id))
        if not is_capital:
            cards.append(RoutedCard(1, Subdeck.CITIES.deck_id))
        tags = [tag("City"), *region_tags(country)] + ([tag("Capital")] if is_capital else [])
        self._add(self.city_model, CITY_FIELDS, values, cards, tags, city["id"], country["id"])

    def write(self, output: pathlib.Path) -> None:
        if self.media.missing:
            examples = ", ".join(str(path) for path in self.media.missing[:3])
            raise MissingMediaError(f"{len(self.media.missing)} media files are missing, e.g. {examples}")
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
