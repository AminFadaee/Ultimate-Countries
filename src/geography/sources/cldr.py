from dataclasses import dataclass

import requests

from geography.sources.http import TIMEOUT
from geography.sources.iso639 import LanguageCodes

CLDR_URL = "https://raw.githubusercontent.com/unicode-org/cldr-json/main/cldr-json"
TERRITORY_INFO_URL = f"{CLDR_URL}/cldr-core/supplemental/territoryInfo.json"
LANGUAGE_NAMES_URL = f"{CLDR_URL}/cldr-localenames-full/main/en/languages.json"


@dataclass(frozen=True)
class SpokenLanguage:
    code: str
    name: str
    percent: float


@dataclass(frozen=True)
class LanguageUse:
    version: str
    territories: dict[str, list[SpokenLanguage]]


def fetch_json(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def merge(entries: dict[str, float], codes: LanguageCodes) -> dict[str, float]:
    merged = {}
    for code, percent in entries.items():
        macrolanguage = codes.macrolanguage_of.get(code)
        target = macrolanguage if macrolanguage in entries else code
        merged[target] = max(merged.get(target, 0.0), percent)
    return merged


def fetch_language_use(session: requests.Session, codes: LanguageCodes) -> LanguageUse:
    supplemental = fetch_json(session, TERRITORY_INFO_URL)["supplemental"]
    names = fetch_json(session, LANGUAGE_NAMES_URL)["main"]["en"]["localeDisplayNames"]["languages"]
    name_by_part3 = {codes.part3(code): name for code, name in names.items() if "-" not in code}

    territories = {}
    for territory, info in supplemental["territoryInfo"].items():
        entries = {}
        for code, details in info.get("languagePopulation", {}).items():
            part3 = codes.part3(code)
            entries[part3] = max(entries.get(part3, 0.0), float(details["_populationPercent"]))
        territories[territory] = sorted(
            (
                SpokenLanguage(code, name_by_part3.get(code) or codes.names.get(code) or code, percent)
                for code, percent in merge(entries, codes).items()
            ),
            key=lambda language: -language.percent,
        )
    return LanguageUse(supplemental["version"]["_cldrVersion"], territories)
