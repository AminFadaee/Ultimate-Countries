import json
import pathlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from geography.naming import slugify

LANGUAGE_CODE_LIKE = re.compile(r"^[a-z]{2,3}$")
SMALL_CAPITAL = 50_000
LARGE_COUNTRY = 5_000_000
SMALL_NOTABLE_CITY = 10_000
RELIGION_TOTAL_RANGE = (97.0, 103.0)


class Outcome(StrEnum):
    CORRECT = "correct"
    WRONG = "wrong"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Check:
    field: str
    outcome: Outcome
    detail: str = ""


@dataclass(frozen=True)
class Anomaly:
    country: str
    check: str
    detail: str


@dataclass
class FieldScore:
    correct: int = 0
    wrong: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float | None:
        checked = self.correct + self.wrong
        return self.correct / checked if checked else None


def key(name: str | None) -> str:
    return slugify(name or "")


def matches(value: str, accepted: list[str]) -> bool:
    return key(value) in {key(option) for option in accepted}


def load_documents(countries_dir: pathlib.Path) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(countries_dir.glob("*.json"))]


def national_languages(document: dict) -> list[str]:
    return [language["name"] for language in document["languages"] if language["official"] == "national"]


def notable_cities(document: dict) -> list[str]:
    return [city["name"] for city in document["cities"]]


def check_capitals(document: dict, expected: dict) -> Check:
    capitals = document["capitals"]
    correct = bool(capitals) and all(matches(capital, expected["capitals"]) for capital in capitals)
    return Check("capital", Outcome.CORRECT if correct else Outcome.WRONG, f"{capitals}")


def check_demonym(document: dict, expected: dict) -> Check:
    demonym = document["demonym"]["male"]
    correct = demonym is not None and matches(demonym, expected["demonym"])
    return Check("demonym", Outcome.CORRECT if correct else Outcome.WRONG, f"{demonym!r}")


def check_currencies(document: dict, expected: dict) -> Check:
    codes = {currency["code"] for currency in document["currencies"]}
    correct = codes == set(expected["currencies"])
    return Check("currency", Outcome.CORRECT if correct else Outcome.WRONG, f"{sorted(codes)}")


def check_official_languages(document: dict, expected: dict) -> Check:
    national = national_languages(document)
    groups = expected["official_languages"]
    if not groups:
        return Check("official_languages", Outcome.SKIPPED)
    missing = [group[0] for group in groups if not any(matches(name, group) for name in national)]
    allowed = [name for group in groups for name in group] + expected["optional_languages"]
    extra = [name for name in national if not matches(name, allowed)]
    correct = not missing and not extra
    return Check("official_languages", Outcome.CORRECT if correct else Outcome.WRONG, f"missing={missing} extra={extra}")


def check_state_religion(document: dict, expected: dict) -> Check:
    religions = document["official_religions"]
    if not expected["state_religion"]:
        correct = not religions
    else:
        correct = bool(religions) and all(matches(religion, expected["state_religion"]) for religion in religions)
    return Check("state_religion", Outcome.CORRECT if correct else Outcome.WRONG, f"{religions}")


def check_notable_cities(document: dict, expected: dict) -> Check:
    if not expected["notable_cities"]:
        return Check("notable_cities", Outcome.SKIPPED)
    cities = notable_cities(document)
    missing = [city for city in expected["notable_cities"] if not any(matches(name, [city]) for name in cities)]
    return Check("notable_cities", Outcome.WRONG if missing else Outcome.CORRECT, f"missing={missing}")


def check_memberships(document: dict, expected: dict) -> Check:
    memberships = set(document["memberships"])
    missing = [organization for organization in expected["member_of"] if organization not in memberships]
    wrong = [organization for organization in expected["not_member_of"] if organization in memberships]
    correct = not missing and not wrong
    return Check("memberships", Outcome.CORRECT if correct else Outcome.WRONG, f"missing={missing} unexpected={wrong}")


CHECKS = (
    check_capitals,
    check_demonym,
    check_currencies,
    check_official_languages,
    check_state_religion,
    check_notable_cities,
    check_memberships,
)


def score(documents: list[dict], reference: dict[str, dict]) -> dict[str, FieldScore]:
    by_code = {document["codes"]["alpha_3"]: document for document in documents}
    scores = defaultdict(FieldScore)
    for code, expected in reference.items():
        document = by_code.get(code)
        if document is None:
            scores["coverage"].wrong += 1
            scores["coverage"].failures.append(f"{code}: missing from dataset")
            continue
        for check in CHECKS:
            result = check(document, expected)
            field_score = scores[result.field]
            if result.outcome is Outcome.CORRECT:
                field_score.correct += 1
            elif result.outcome is Outcome.WRONG:
                field_score.wrong += 1
                field_score.failures.append(f"{document['name']}: {result.detail}")
            else:
                field_score.skipped += 1
    return dict(scores)


def anomalies(documents: list[dict]) -> list[Anomaly]:
    found = []
    for document in documents:
        name = document["name"]
        status = document["status"]
        capitals = [city for city in document["cities"] if city["role"] == "capital"]
        population = document["population"] or 0

        if status["sovereign"] and not status["disputed"]:
            for label, present in (
                ("missing capital", capitals),
                ("missing currency", document["currencies"]),
                ("missing official language", national_languages(document) or document["languages"]),
            ):
                if not present:
                    found.append(Anomaly(name, label, ""))
        for capital in capitals:
            if population > LARGE_COUNTRY and capital["population"] is not None and capital["population"] < SMALL_CAPITAL:
                found.append(Anomaly(name, "small capital", f"{capital['name']} {capital['population']}"))
        for city in document["cities"]:
            if city["population"] is not None and population and city["population"] > population:
                found.append(Anomaly(name, "city larger than country", f"{city['name']} {city['population']}"))
            if city["role"] == "notable" and city["population"] is not None and city["population"] < SMALL_NOTABLE_CITY:
                found.append(Anomaly(name, "small notable city", f"{city['name']} {city['population']}"))

        names = Counter(key(language["name"]) for language in document["languages"])
        for duplicate in [language for language, count in names.items() if count > 1]:
            found.append(Anomaly(name, "duplicate language", duplicate))
        for language in document["languages"]:
            if language["percent"] == 0:
                found.append(Anomaly(name, "zero-percent language", language["name"]))
            if LANGUAGE_CODE_LIKE.match(language["name"]):
                found.append(Anomaly(name, "language named by code", language["name"]))

        total = sum(religion["percent"] for religion in document["religions"])
        if document["religions"] and not RELIGION_TOTAL_RANGE[0] <= total <= RELIGION_TOTAL_RANGE[1]:
            found.append(Anomaly(name, "religions do not sum to 100", f"{total:.1f}"))
        if document["demonym"]["male"] and key(document["demonym"]["male"]) == key(name):
            found.append(Anomaly(name, "demonym equals name", document["demonym"]["male"]))
        if status["dependency_type"] == "disputed" and not status["disputed"]:
            found.append(Anomaly(name, "disputed flag contradiction", ""))

        independence = document["independence"] or {}
        if independence and key(independence["from"] or "") == key(name):
            found.append(Anomaly(name, "independence from itself", independence["from"]))
    return found


def format_report(scores: dict[str, FieldScore], found: list[Anomaly], show_failures: bool) -> str:
    lines = ["Reference accuracy"]
    for name, field_score in sorted(scores.items()):
        accuracy = field_score.accuracy
        shown = "n/a" if accuracy is None else f"{accuracy:.0%}"
        lines.append(f"  {name:20} {shown:>5}  ({field_score.correct} ok, {field_score.wrong} wrong, {field_score.skipped} skipped)")
        if show_failures:
            lines.extend(f"      - {failure}" for failure in field_score.failures)
    lines.append("")
    lines.append(f"Anomalies ({len(found)})")
    for check, count in Counter(anomaly.check for anomaly in found).most_common():
        lines.append(f"  {check:42} {count}")
    if show_failures:
        lines.extend(f"      - {anomaly.country}: {anomaly.check} {anomaly.detail}" for anomaly in found)
    return "\n".join(lines)
