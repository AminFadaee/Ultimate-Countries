from difflib import SequenceMatcher

from geography.models import Language, OfficialStatus
from geography.sources.cldr import SpokenLanguage
from geography.sources.iso639 import LanguageCodes
from geography.sources.wikidata import OfficialLanguage
from geography.sources.wikipedia import ListedLanguage

NAME_SIMILARITY = 0.8
MIN_REGIONAL_COVERAGE = 50.0
MIN_SPOKEN_SHARE = 0.1
GROUP_PREFIX = "languages of"


def related_codes(language: OfficialLanguage, codes: LanguageCodes) -> set[str]:
    part3 = {codes.part3(code) for code in language.codes}
    return part3 | {codes.macrolanguage_of[code] for code in part3 if code in codes.macrolanguage_of}


def covers(related: set[str], code: str, codes: LanguageCodes) -> bool:
    return code in related or codes.macrolanguage_of.get(code) in related


def same_name(first: str, second: str) -> bool:
    return any(
        SequenceMatcher(None, word.casefold(), second.casefold()).ratio() >= NAME_SIMILARITY
        for word in [first, *first.split()]
    )


def official_status(language: OfficialLanguage) -> OfficialStatus:
    return OfficialStatus.REGIONAL if language.regional else OfficialStatus.NATIONAL


def merge_languages(
    official: list[OfficialLanguage],
    spoken: list[SpokenLanguage],
    codes: LanguageCodes,
) -> list[Language]:
    own_codes = {id(language): {codes.part3(code) for code in language.codes} for language in official}
    matches = {id(language): related_codes(language, codes) for language in official}
    matched = set()
    languages = []
    for use in spoken:
        match = next((language for language in official if use.code in own_codes[id(language)]), None)
        if match is None:
            match = next((language for language in official if covers(matches[id(language)], use.code, codes)), None)
        if match is None:
            match = next((language for language in official if not language.codes and same_name(language.name, use.name)), None)
        if match is None:
            languages.append(Language(use.name, use.code, None, use.percent))
            continue
        matched.add(id(match))
        languages.append(Language(match.name, use.code, official_status(match), use.percent))

    for language in official:
        if id(language) not in matched:
            code = next(iter(sorted(codes.part3(c) for c in language.codes)), None)
            languages.append(Language(language.name, code, official_status(language), None))
    folded = fold_dialects(fold_variants(languages), codes)
    return by_usage(nationalized(deduplicated(absorb_synonyms(cleaned(folded, codes), codes))))


def head_word(name: str) -> str:
    return name.split()[-1]


def abbreviates(listed: str, name: str) -> bool:
    acronym, *rest = listed.split()
    words = name.split()
    if len(acronym) < 2 or not acronym.isupper() or not rest or len(words) <= len(rest):
        return False
    qualifier, tail = words[: -len(rest)], words[-len(rest):]
    return [word.casefold() for word in tail] == [word.casefold() for word in rest] and "".join(
        word[0] for word in qualifier
    ).upper() == acronym


def named_in(language: Language, listed: list[ListedLanguage]) -> bool:
    return any(
        same_name(name, language.name) or same_name(head_word(language.name), name) or abbreviates(name, language.name)
        for entry in listed
        for name in entry.names
    )


def with_status(
    languages: list[Language],
    national: list[ListedLanguage],
    regional: list[ListedLanguage],
) -> list[Language]:
    def status(language: Language) -> OfficialStatus | None:
        if named_in(language, national):
            return OfficialStatus.NATIONAL
        if named_in(language, regional):
            return OfficialStatus.REGIONAL
        return None

    updated = [Language(language.name, language.code, status(language), language.percent) for language in languages]
    for entry in national:
        if not any(named_in(language, [entry]) for language in updated):
            updated.append(Language(entry.name, None, OfficialStatus.NATIONAL, None))
    return by_usage(updated)


def is_junk(language: Language, codes: LanguageCodes) -> bool:
    if language.name.casefold().startswith(GROUP_PREFIX):
        return True
    if language.official:
        return False
    return (
        language.code in codes.constructed
        or language.name == language.code
        or language.percent is None
        or language.percent < MIN_SPOKEN_SHARE
    )


def cleaned(languages: list[Language], codes: LanguageCodes) -> list[Language]:
    return [language for language in languages if not is_junk(language, codes)]


def same_language(spoken: Language, synonym: Language, codes: LanguageCodes) -> bool:
    if synonym.code is None:
        return spoken.code is not None and same_name(synonym.name, spoken.name)
    if spoken.code is None:
        return False
    family = {spoken.code, codes.macrolanguage_of.get(spoken.code)} - {None}
    return synonym.code in family or codes.macrolanguage_of.get(synonym.code) in family


def absorb_synonyms(languages: list[Language], codes: LanguageCodes) -> list[Language]:
    merged = list(languages)
    for synonym in [language for language in languages if language.official and language.percent is None]:
        spoken = next((language for language in merged if language.percent is not None and same_language(language, synonym, codes)), None)
        if spoken is None:
            continue
        official = spoken.official or synonym.official
        replacement = Language(spoken.name, spoken.code, official, spoken.percent)
        merged = [replacement if language is spoken else language for language in merged if language is not synonym]
    return merged


def deduplicated(languages: list[Language]) -> list[Language]:
    best = {}
    for language in languages:
        known = best.get(language.name)
        if known is None or (language.percent or 0) > (known.percent or 0):
            best[language.name] = language
    return list(best.values())


def nationalized(languages: list[Language]) -> list[Language]:
    if any(language.official is OfficialStatus.NATIONAL for language in languages):
        return languages
    regional = [language for language in languages if language.official is OfficialStatus.REGIONAL]
    if sum(language.percent or 0 for language in regional) < MIN_REGIONAL_COVERAGE:
        return languages
    return [
        Language(language.name, language.code, OfficialStatus.NATIONAL, language.percent) if language in regional else language
        for language in languages
    ]


def is_variant(name: str, base: str) -> bool:
    return name != base and name.endswith(f" {base}")


def preferred(first: Language, second: Language) -> Language:
    rank = {OfficialStatus.NATIONAL: 0, OfficialStatus.REGIONAL: 1, None: 2}
    return min(first, second, key=lambda language: rank[language.official])


def fold_variants(languages: list[Language]) -> list[Language]:
    merged = list(languages)
    for base in [language for language in languages if language.percent is not None]:
        for variant in [language for language in merged if is_variant(language.name, base.name) and language.percent is not None]:
            current = next(language for language in merged if language.name == base.name)
            keep = preferred(current, variant)
            combined = Language(keep.name, keep.code, keep.official, max(current.percent, variant.percent))
            merged = [combined if language is current else language for language in merged if language is not variant]
    return merged


def belongs_to(language: Language, umbrella: Language, codes: LanguageCodes) -> bool:
    if is_variant(language.name, umbrella.name):
        return True
    return umbrella.code is not None and codes.macrolanguage_of.get(language.code) == umbrella.code


def fold_dialects(languages: list[Language], codes: LanguageCodes) -> list[Language]:
    merged = list(languages)
    for umbrella in [language for language in languages if language.official and language.percent is None]:
        dialects = [language for language in merged if belongs_to(language, umbrella, codes) and language.percent is not None]
        if not dialects:
            continue
        total = min(100.0, sum(dialect.percent for dialect in dialects))
        combined = Language(umbrella.name, umbrella.code, umbrella.official, round(total, 2))
        merged = [combined if language is umbrella else language for language in merged if language not in dialects]
    return merged


def by_usage(languages: list[Language]) -> list[Language]:
    return sorted(languages, key=lambda language: -(language.percent if language.percent is not None else -1))
