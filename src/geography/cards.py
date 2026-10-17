from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

WORLD_BANK = "world_bank"
DISPUTED = "disputed"
NATIONAL = "national"
MAX_LANGUAGES = 4
SIGNIFICANT_DIGITS = 2
WHOLE_FROM = 10
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


QUESTION_TYPES = (CardType.CAPITAL, CardType.FLAG, CardType.MAP)


@dataclass(frozen=True)
class Answer:
    text: str
    context: list[str] = field(default_factory=list)


def joined(items: list[str]) -> str:
    return ", ".join(items)


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
    male = document["demonym"]["male"]
    return Answer(male) if male else None


def language(document: dict) -> Answer | None:
    languages = document["languages"]
    official = [entry["name"] for entry in languages if entry["official"] == NATIONAL]
    spoken = [entry["name"] for entry in languages if entry["percent"] is not None]
    if official:
        shown = official[:MAX_LANGUAGES]
        hidden = len(official) - len(shown)
        return Answer(joined(shown) + (f" +{hidden} more" if hidden else ""))
    return Answer(f"{spoken[0]} (most spoken; no official language)") if spoken else None


def currency(document: dict) -> Answer | None:
    main = [entry["name"] for entry in document["currencies"] if entry["main"]]
    return Answer(joined(main)) if main else None


def religion(document: dict) -> Answer | None:
    religions = [entry for entry in document["religions"] if entry["percent"]]
    if document["official_religions"]:
        return Answer(joined(document["official_religions"]))
    if not religions or religions[0]["religion"] == OTHER_RELIGIONS:
        return None
    return Answer(f"{religions[0]['religion']} (largest; no state religion)")


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
    return Answer(f"{text} ({year})" if year else text)


def government(document: dict) -> Answer | None:
    form = document["government"]["form"]
    if not document["status"]["sovereign"] or not form:
        return None
    return Answer(form)


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


@dataclass(frozen=True)
class Context:
    region: str
    status: str | None
    notable_cities: str | None


def recognised(count: int, names: list[str]) -> str:
    text = f"recognised by {count} UN member{'' if count == 1 else 's'}"
    return f"{text} ({joined(names)})" if names else text


def status_line(document: dict) -> str | None:
    status, recognition = document["status"], document["recognition"]
    parts = []
    if status["dependency_type"] == DISPUTED:
        parts.append("Disputed territory")
    if recognition and recognition["limited"]:
        parts.append("State with limited recognition")
    if status["un_observer"]:
        parts.append("UN observer state")
    if recognition and recognition["recognised_by_un_members"]:
        parts.append(recognised(recognition["recognised_by_un_members"], recognition["recognised_by"]))
    if recognition and recognition["claimants"]:
        wording = "claimed by" if status["dependency_type"] == DISPUTED else "other claimants:"
        parts.append(f"{wording} {joined(recognition['claimants'])}")
    return " · ".join(parts) or None


def shared_context(document: dict) -> Context:
    region = " · ".join(part for part in (document["region"], document["subregion"]) if part)
    notable = [city["name"] for city in document["cities"] if city["role"] == "notable"]
    return Context(region, status_line(document), joined(notable) if notable else None)


def coverage(documents: list[dict]) -> dict[CardType, int]:
    built = [answers(document) for document in documents]
    return {card_type: sum(card_type in found for found in built) for card_type in CardType}


def format_coverage(documents: list[dict]) -> str:
    counts = coverage(documents)
    lines = [f"Card coverage ({len(documents)} entries)"]
    lines.extend(f"  {card_type:12} {counts[card_type]:4}" for card_type in QUESTION_TYPES)
    lines.append("Fact coverage")
    lines.extend(f"  {card_type:12} {count:4}" for card_type, count in counts.items() if card_type not in QUESTION_TYPES)
    return "\n".join(lines)
