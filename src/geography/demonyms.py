import re
from difflib import SequenceMatcher

SPELLING_SIMILARITY = 0.88
COMBINED_MARKERS = (",", " or ", " and ")
COMBINED_SEPARATOR = re.compile("|".join(map(re.escape, COMBINED_MARKERS)))


def similarity(first: str, second: str) -> float:
    return SequenceMatcher(None, first.casefold(), second.casefold()).ratio()


def corrected_demonym(current: str | None, country_name: str, options: list[str]) -> str | None:
    if not options or current is None:
        return current or (options[0] if options else None)
    if current.casefold() in {option.casefold() for option in options}:
        return current
    if any(marker in current for marker in COMBINED_MARKERS):
        return current
    candidates = [option for option in options if option.casefold() != country_name.casefold()]
    if current.casefold() == country_name.casefold():
        return candidates[0] if candidates else current
    return next((option for option in candidates if similarity(current, option) >= SPELLING_SIMILARITY), current)


def is_plural_of(option: str, others: set[str]) -> bool:
    return any(option.casefold() == f"{other.casefold()}s" for other in others)


def other_demonyms(main: str | None, female: str | None, country_name: str, options: list[str]) -> list[str]:
    parts = COMBINED_SEPARATOR.split(main) if main else []
    known = {name.strip().casefold() for name in (main, female, country_name, *parts) if name}
    others = []
    for option in options:
        if option.casefold() in known or is_plural_of(option, set(options) | known):
            continue
        if main and similarity(option, main) >= SPELLING_SIMILARITY:
            continue
        others.append(option)
        known.add(option.casefold())
    return others
