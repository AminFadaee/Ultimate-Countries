import re

from geography.models import Independence
from geography.sources.wikidata import Inception
from geography.sources.wikipedia import FormationRecord, IndependenceRecord

YEAR = re.compile(r"\d{3,4}")
INDEPENDENCE_WORD = "independence"


def year_of(date: str | None) -> int | None:
    match = YEAR.search(date or "")
    return int(match.group()) if match else None


def agrees(first: str | None, second: str | None) -> bool:
    year = year_of(first)
    return year is not None and year == year_of(second)


def chosen_inception(inceptions: list[Inception]) -> Inception | None:
    preferred = [inception for inception in inceptions if inception.preferred]
    candidates = preferred or inceptions
    return candidates[0] if len(candidates) == 1 else None


def established(formation: FormationRecord | None, inceptions: list[Inception]) -> str | None:
    inception = chosen_inception(inceptions)
    if formation is None or inception is None or not agrees(formation.acquired, inception.date):
        return None
    return max((formation.acquired, inception.date), key=len)


def independence(
    records: list[IndependenceRecord],
    formation: FormationRecord | None,
    inceptions: list[Inception],
) -> Independence | None:
    evidence = [inception.date for inception in inceptions]
    if formation:
        evidence += [formation.acquired, formation.subordination_ended]
    confirmed = [record for record in records if any(agrees(record.date, date) for date in evidence)]
    if not confirmed:
        return None
    latest = max(
        confirmed,
        key=lambda record: (year_of(record.date), INDEPENDENCE_WORD in record.event.casefold(), record.date),
    )
    date = latest.date if latest.date in evidence else str(year_of(latest.date))
    return Independence(date, latest.from_power)
