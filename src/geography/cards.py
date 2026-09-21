from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

WORLD_BANK = "world_bank"
NATIONAL = "national"
REGIONAL = "regional"
MAX_CONTEXT_ITEMS = 5
SIGNIFICANT_DIGITS = 2
WHOLE_FROM = 10
UNAFFILIATED = "Unaffiliated"
OTHER_RELIGIONS = "Other religions"
SCALES = ((1_000_000_000, "billion"), (1_000_000, "million"))


class CardType(StrEnum):
    CAPITAL = "Capital"
    FLAG = "Flag"
    MAP = "Map"
    DEMONYM = "Demonym"
    LANGUAGE = "Language"
    CURRENCY = "Currency"
    RELIGION = "Religion"
    POPULATION = "Population"
    GOVERNMENT = "Government"


@dataclass(frozen=True)
class Answer:
    text: str
    context: list[str] = field(default_factory=list)


def joined(items: list[str]) -> str:
    return ", ".join(items)


def with_percent(name: str, percent: float | None, note: str | None = None) -> str:
    details = [f"{percent:g}%" if percent is not None else None, note]
    shown = [detail for detail in details if detail]
    return f"{name} ({', '.join(shown)})" if shown else name


def capital(document: dict) -> Answer | None:
    capitals = document["capitals"]
    if not capitals:
        return None
    return Answer(joined(capitals), [f"{len(capitals)} capitals"] if len(capitals) > 1 else [])


def flag(document: dict) -> Answer | None:
    return Answer(document["name"]) if document["flag"] else None


def country_map(document: dict) -> Answer | None:
    return Answer(document["name"]) if document["map"] else None


def demonym(document: dict) -> Answer | None:
    names = document["demonym"]
    if not names["male"]:
        return None
    context = [f"Female: {names['female']}"] if names["female"] != names["male"] else []
    if names["others"]:
        context.append(f"Also: {joined(names['others'])}")
    return Answer(names["male"], context)


def language(document: dict) -> Answer | None:
    languages = document["languages"]
    official = [entry for entry in languages if entry["official"] == NATIONAL]
    spoken = [entry for entry in languages if entry["percent"] is not None]
    if official:
        answer, note = official, None
    elif spoken:
        answer, note = spoken[:1], "No official language; most widely spoken"
    else:
        return None
    rest = [entry for entry in languages if entry not in answer and (entry["percent"] is not None or entry["official"])]
    context = [note] if note else []
    if rest:
        context.append("Also: " + joined([describe_language(entry) for entry in rest[:MAX_CONTEXT_ITEMS]]))
    return Answer(joined([entry["name"] for entry in answer]), context)


def describe_language(entry: dict) -> str:
    return with_percent(entry["name"], entry["percent"], "regional" if entry["official"] == REGIONAL else None)


def currency(document: dict) -> Answer | None:
    currencies = document["currencies"]
    main = [entry["name"] for entry in currencies if entry["main"]]
    if not main:
        return None
    others = [entry["name"] for entry in currencies if not entry["main"]]
    return Answer(joined(main), [f"Also used: {joined(others)}"] if others else [])


def religion(document: dict) -> Answer | None:
    religions = [entry for entry in document["religions"] if entry["percent"]]
    breakdown = joined([with_percent(entry["religion"], entry["percent"]) for entry in religions[:MAX_CONTEXT_ITEMS]])
    if document["official_religions"]:
        context = ["State religion"] + ([breakdown] if breakdown else [])
        return Answer(joined(document["official_religions"]), context)
    if not religions or religions[0]["religion"] == OTHER_RELIGIONS:
        return None
    largest = religions[0]
    name = "Unaffiliated (no religion)" if largest["religion"] == UNAFFILIATED else largest["religion"]
    return Answer(name, ["Largest religion; no state religion", breakdown])


def rounded(value: int) -> str:
    for scale, word in SCALES:
        if value >= scale:
            scaled = value / scale
            decimals = 0 if round(scaled, 1) >= WHOLE_FROM else 1
            return f"{f'{scaled:.{decimals}f}'.removesuffix('.0')} {word}"
    return f"{int(float(f'{value:.{SIGNIFICANT_DIGITS}g}')):,}"


def population(document: dict) -> Answer | None:
    value = document["population"]
    if not value:
        return None
    provenance = document["sources"].get("population", {})
    year = provenance.get("as_of") if provenance.get("source") == WORLD_BANK else None
    text = f"~{rounded(value)}"
    return Answer(f"{text} ({year})" if year else text, [f"{value:,}"])


def government(document: dict) -> Answer | None:
    form = document["government"]["form"]
    if not document["status"]["sovereign"] or not form:
        return None
    label = document["government"]["label"]
    return Answer(form, [label] if label and label.casefold() != form.casefold() else [])


BUILDERS: dict[CardType, Callable[[dict], Answer | None]] = {
    CardType.CAPITAL: capital,
    CardType.FLAG: flag,
    CardType.MAP: country_map,
    CardType.DEMONYM: demonym,
    CardType.LANGUAGE: language,
    CardType.CURRENCY: currency,
    CardType.RELIGION: religion,
    CardType.POPULATION: population,
    CardType.GOVERNMENT: government,
}


def answers(document: dict) -> dict[CardType, Answer]:
    built = {card_type: builder(document) for card_type, builder in BUILDERS.items()}
    return {card_type: answer for card_type, answer in built.items() if answer is not None}


def shared_context(document: dict) -> list[str]:
    region = " · ".join(part for part in (document["region"], document["subregion"]) if part)
    notable = [city["name"] for city in document["cities"] if city["role"] == "notable"]
    context = [region] if region else []
    if notable:
        context.append(f"Notable cities: {joined(notable)}")
    return context


def coverage(documents: list[dict]) -> dict[CardType, int]:
    built = [answers(document) for document in documents]
    return {card_type: sum(card_type in found for found in built) for card_type in CardType}


def format_coverage(documents: list[dict]) -> str:
    lines = [f"Card coverage ({len(documents)} entries)"]
    lines.extend(f"  {card_type:12} {count:4}" for card_type, count in coverage(documents).items())
    return "\n".join(lines)
