import hashlib
import html
import logging
import pathlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

import genanki

from geography.cards import Answer, CardType, Context, answers, shared_context
from geography.models import CityRole
from geography.naming import slugify

DECK_NAME = "Ultimate Countries"
COUNTRY_MODEL_ID = 1_730_418_562
CITY_MODEL_ID = 1_730_418_563
TAG_PREFIX = "UC"
MEDIA_PREFIX = "uc"
ULTIMATE_GEOGRAPHY_URL = "https://github.com/anki-geo/ultimate-geography"
REPOSITORY_URL = "https://github.com/AminFadaee/Ultimate-Countries"
COUNTRIES_ANKIWEB_URL = "https://ankiweb.net/shared/info/164142051"
PLACES_ANKIWEB_URL = "https://ankiweb.net/shared/info/216393289"
TAG_UNSAFE = re.compile(r"[^\w-]+")
REGION_CONTEXT = "{{Region}}"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Template:
    name: str
    requires: str
    front: str
    back: str

    def as_genanki(self, script: str = "") -> dict:
        return {"name": self.name, "qfmt": self.front + script, "afmt": self.back + script}


@dataclass(frozen=True)
class Fact:
    card_type: CardType
    field: str
    label: str


class SelectedNote(genanki.Note):
    def __init__(self, ords: list[int], **kwargs):
        super().__init__(**kwargs)
        self.ords = ords

    @property
    def cards(self) -> list[genanki.Card]:
        return [genanki.Card(ord) for ord in self.ords]


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


def details(info_field: str | None, context: str, extra: str = "", facts: str = "") -> str:
    info = conditional(info_field, f'<div class="info">{placeholder(info_field)}</div>') if info_field else ""
    return f'{info}{extra}<div class="context">{context}</div>{facts}'


def ask_attribute(
    label: str,
    field: str,
    info_field: str | None = None,
    subject: str = COUNTRY_NAME,
    context: str = REGION_CONTEXT,
    facts: str = "",
) -> tuple[str, str]:
    front = face(entity(subject), label, UNKNOWN_VALUE)
    back = face(entity(subject), label, value(placeholder(field), answered=True)) + details(info_field, context, facts=facts)
    return front, back


def ask_entity(
    label: str,
    shown: str,
    answer: str = COUNTRY_NAME,
    info_field: str | None = None,
    context: str = REGION_CONTEXT,
    extra: str = "",
    facts: str = "",
    shown_back: str | None = None,
) -> tuple[str, str]:
    front = face(UNKNOWN_ENTITY, label, shown)
    back = face(entity(answer, answered=True), label, shown_back or shown) + details(info_field, context, extra, facts)
    return front, back


def guarded(field: str, faces: tuple[str, str]) -> tuple[str, str]:
    front, back = faces
    return conditional(field, front), back


FACTS = (
    Fact(CardType.DEMONYM, "Demonym", "Demonym"),
    Fact(CardType.LANGUAGE, "Language", "Languages"),
    Fact(CardType.CURRENCY, "Currency", "Currency"),
    Fact(CardType.RELIGION, "Religion", "Religion"),
    Fact(CardType.POPULATION, "Population", "Population"),
    Fact(CardType.GOVERNMENT, "Government", "Government"),
)


def chip(field: str, label: str) -> str:
    return conditional(field, f'<span class="chip"><i>{label}</i>{placeholder(field)}</span>')


FACT_CHIPS = '<div class="facts">' + "".join(chip(fact.field, fact.label) for fact in FACTS) + chip("NotableCities", "Cities") + "</div>"

COUNTRY_TEMPLATES = (
    Template(
        "Capital",
        "Capital",
        *guarded("Capital", ask_attribute("Capital", "Capital", "CapitalInfo", facts=FACT_CHIPS)),
    ),
    Template(
        "Capital of",
        "Capital",
        *guarded("Capital", ask_entity("Capital", value(placeholder("Capital")), info_field="CapitalInfo", facts=FACT_CHIPS)),
    ),
    Template(
        "Flag",
        "Flag",
        *guarded("Flag", ask_entity("Flag", image("Flag", "flag"), answer="{{Name}}", extra=MAP_THUMBNAIL, facts=FACT_CHIPS)),
    ),
    Template("Map", "Map", *guarded("Map", ask_entity("Map", image("Map", "map"), facts=FACT_CHIPS))),
)

CAPITAL_OF_CARD = 1

COUNTRY_FIELDS = (
    "Id",
    "Name",
    "Capital",
    "CapitalInfo",
    "Flag",
    "Map",
    *(fact.field for fact in FACTS),
    "Region",
    "NotableCities",
)

CITY_MAP_FRONT, CITY_MAP_BACK = ask_entity(
    "Map", image("Map", "map"), answer='{{City}}<div class="subtitle">{{Country}}</div>', shown_back=image("MapAnswer", "map")
)
CITY_COUNTRY_FRONT, CITY_COUNTRY_BACK = ask_attribute("Country", "Country", subject="{{City}}")

CITY_TEMPLATES = (
    Template("Map", "Map", conditional("Map", CITY_MAP_FRONT), CITY_MAP_BACK),
    Template("Country", "City", f"{{{{^IsCapital}}}}{CITY_COUNTRY_FRONT}{{{{/IsCapital}}}}", CITY_COUNTRY_BACK),
)

CITY_MAP_CARD, CITY_COUNTRY_CARD = range(len(CITY_TEMPLATES))
CITY_FIELDS = ("Id", "City", "Country", "Map", "IsCapital", "Region", "MapAnswer")

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

FACTS_CSS = """
.facts { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; max-width: 420px; margin: 12px auto 0; }
.chip { border-radius: 16px; padding: 5px 12px; background: rgba(128, 128, 128, .13); font-size: 14px; color: var(--text); }
.chip i { font-style: normal; color: var(--muted); font-size: 11px; letter-spacing: .06em; text-transform: uppercase; margin-right: 6px; }
"""

MAP_ZOOM_CSS = """
.map img { cursor: zoom-in; }
.zoom-overlay { position: fixed; inset: 0; z-index: 1000; overflow: auto; background: rgba(0, 0, 0, .88); cursor: grab;
  -webkit-overflow-scrolling: touch; }
.zoom-overlay.dragging { cursor: grabbing; }
.zoom-stage { display: flex; min-width: 100%; min-height: 100%; width: max-content; height: max-content; }
.zoom-stage img { margin: auto; max-width: none; max-height: none; }
"""

MAP_ZOOM_SCRIPT = """
<script>
(function () {
  var ZOOM = 1.25;
  var root = document.getElementById("qa") || document.body;

  function open(image, event) {
    var bounds = image.getBoundingClientRect();
    var focusX = (event.clientX - bounds.left) / bounds.width;
    var focusY = (event.clientY - bounds.top) / bounds.height;
    var fit = Math.min(window.innerWidth / image.naturalWidth, window.innerHeight / image.naturalHeight);
    var overlay = document.createElement("div");
    var stage = document.createElement("div");
    var large = document.createElement("img");
    overlay.className = "zoom-overlay";
    stage.className = "zoom-stage";
    large.src = image.src;
    large.style.width = image.naturalWidth * fit * ZOOM + "px";
    large.style.height = image.naturalHeight * fit * ZOOM + "px";
    stage.appendChild(large);
    overlay.appendChild(stage);
    root.appendChild(overlay);
    overlay.scrollLeft = large.offsetLeft + focusX * large.offsetWidth - overlay.clientWidth / 2;
    overlay.scrollTop = large.offsetTop + focusY * large.offsetHeight - overlay.clientHeight / 2;
    pannable(overlay);
  }

  function pannable(overlay) {
    var drag = null;
    overlay.addEventListener("pointerdown", function (event) {
      if (event.pointerType !== "mouse") return;
      drag = { x: event.clientX, y: event.clientY, left: overlay.scrollLeft, top: overlay.scrollTop, moved: false };
      overlay.classList.add("dragging");
    });
    overlay.addEventListener("pointermove", function (event) {
      if (!drag) return;
      var dx = event.clientX - drag.x;
      var dy = event.clientY - drag.y;
      drag.moved = drag.moved || Math.abs(dx) + Math.abs(dy) > 4;
      overlay.scrollLeft = drag.left - dx;
      overlay.scrollTop = drag.top - dy;
    });
    overlay.addEventListener("pointerup", function () {
      overlay.classList.remove("dragging");
      setTimeout(function () { drag = null; }, 0);
    });
    overlay.addEventListener("click", function (event) {
      event.stopPropagation();
      if (!(drag && drag.moved)) overlay.remove();
    });
  }

  root.querySelectorAll(".map img").forEach(function (image) {
    image.addEventListener("click", function (event) {
      event.stopPropagation();
      open(image, event);
    });
  });
})();
</script>
"""


def description() -> str:
    return f"""
<p><b>{DECK_NAME}</b>: countries, territories and their major cities.</p>
<p>Inspired by <a href="{ULTIMATE_GEOGRAPHY_URL}">Ultimate Geography</a>, the deck that got me excited about learning geography.
Ultimate Countries covers every country and territory with a permanent population. You learn capitals, flags and maps, and
the back of every card also shows the country's demonym, languages, currency, religion, population, government and
notable cities. The best-known cities of the world are in the deck too, each on an atlas-style map.</p>
<p>Everything is in one deck. Notes are tagged by kind, region, status and membership (for example <code>UC::City</code>,
<code>UC::Asia</code> or <code>UC::NATO</code>), so you can study one group at a time with a filtered deck: Tools → Create Filtered Deck, then
search for <code>deck:"Ultimate Countries" tag:UC::Asia</code>. You can filter by card type too, for example
<code>deck:"Ultimate Countries" "note:Ultimate Countries country" card:Flag</code>. To skip a kind of card, search for it in the
Browser and suspend the cards rather than deleting them, because deleted cards come back when you import an update.</p>
<p>Tap a country or city map to zoom in on that spot; drag to look around and tap again to close.</p>
<p>Companion deck: <a href="{PLACES_ANKIWEB_URL}">Ultimate Places</a>, with continents, seas, mountains, rivers, deserts,
rainforests, regions and landmarks.</p>
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
    tags = [tag("Country"), *region_tags(document)]
    tags.append(tag("Sovereign" if status["sovereign"] else "Territory"))
    if status["disputed"]:
        tags.append(tag("Disputed"))
    tags.extend(tag(organization) for organization in document["memberships"])
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
    def __init__(self, data_dir: pathlib.Path, build_dir: pathlib.Path, shared_capitals: frozenset[str] = frozenset()):
        self.media = MediaLibrary(data_dir, build_dir / "media")
        self.shared_capitals = shared_capitals
        self.country_model = self._model(
            COUNTRY_MODEL_ID,
            f"{DECK_NAME} country",
            COUNTRY_FIELDS,
            [template.as_genanki(MAP_ZOOM_SCRIPT) for template in COUNTRY_TEMPLATES],
            CSS + FACTS_CSS + MAP_ZOOM_CSS,
        )
        self.city_model = self._model(
            CITY_MODEL_ID,
            f"{DECK_NAME} city",
            CITY_FIELDS,
            [template.as_genanki(MAP_ZOOM_SCRIPT) for template in CITY_TEMPLATES],
            CSS + MAP_ZOOM_CSS,
        )
        self.root = genanki.Deck(stable_id(DECK_NAME), DECK_NAME, description())
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
        for fact in FACTS:
            answer = found.get(fact.card_type)
            values[fact.field] = escaped(answer.text) if answer else ""

        ords = [ord for ord, template in enumerate(COUNTRY_TEMPLATES) if values[template.requires]]
        if self.shared_capitals.intersection(document["capitals"]):
            ords.remove(CAPITAL_OF_CARD)
        self._add(self.country_model, COUNTRY_FIELDS, values, ords, country_tags(document), document["id"])
        capitals = {city["id"] for city in document["cities"] if city["role"] == CityRole.CAPITAL}
        for city in document["famous_cities"]:
            self.add_city(city, document, city["id"] in capitals)

    def add_city(self, city: dict, country: dict, is_capital: bool) -> None:
        values = {
            "Id": city["id"],
            "City": escaped(city["name"]),
            "Country": escaped(country["name"]),
            "Map": self.media.image(city["map"], "city"),
            "MapAnswer": self.media.image(city["answer_map"], "city"),
            "IsCapital": "yes" if is_capital else "",
            "Region": context_html(shared_context(country)),
        }
        ords = [CITY_MAP_CARD] if values["Map"] else []
        if not is_capital:
            ords.append(CITY_COUNTRY_CARD)
        tags = [tag("City"), *region_tags(country)]
        tags += [tag("Capital")] if is_capital else []
        tags += [tag("Most_visited")] if city["most_visited"] else []
        self._add(self.city_model, CITY_FIELDS, values, ords, tags, city["id"], country["id"])

    def write(self, output: pathlib.Path) -> None:
        if self.media.missing:
            examples = ", ".join(str(path) for path in self.media.missing[:3])
            raise MissingMediaError(f"{len(self.media.missing)} media files are missing, e.g. {examples}")
        output.parent.mkdir(parents=True, exist_ok=True)
        package = genanki.Package([self.root], media_files=sorted(set(self.media.files)))
        package.write_to_file(str(output))
        logger.info("Wrote %d notes and %d media files to %s", self.notes, len(set(self.media.files)), output)

    def _add(self, model, fields, values, ords, tags, *identity: str) -> None:
        if not ords:
            return
        note = SelectedNote(
            ords,
            model=model,
            fields=[values[name] for name in fields],
            tags=sorted(set(tags)),
            guid=genanki.guid_for(*identity),
        )
        self.root.add_note(note)
        self.notes += 1

    @staticmethod
    def _model(model_id: int, name: str, fields: tuple[str, ...], templates: list[dict], css: str = CSS) -> genanki.Model:
        return genanki.Model(
            model_id,
            name,
            fields=[{"name": field} for field in fields],
            templates=templates,
            css=css,
            sort_field_index=1,
        )


def shared_capitals(documents: list[dict]) -> frozenset[str]:
    counts = Counter(capital for document in documents for capital in set(document["capitals"]))
    return frozenset(capital for capital, count in counts.items() if count > 1)


def build_deck(documents: list[dict], data_dir: pathlib.Path, build_dir: pathlib.Path, output: pathlib.Path) -> None:
    builder = DeckBuilder(data_dir, build_dir, shared_capitals(documents))
    for document in sorted(documents, key=lambda document: document["name"]):
        builder.add_country(document)
    builder.write(output)
