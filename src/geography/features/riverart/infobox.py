import re
from dataclasses import dataclass, replace

import requests

from geography.sources.http import TIMEOUT

MOUTH = re.compile(r"^\s*\|\s*mouth\s*=\s*(.*)$", re.IGNORECASE)
SOURCE = re.compile(r"^\s*\|\s*(?:source(\d*)|oorsprong)\s*=\s*(.*)$", re.IGNORECASE)
SOURCE_COORDINATES = re.compile(r"^\s*\|\s*source(\d*)_coordinates\s*=\s*(.*)$", re.IGNORECASE)
SOURCE_PLACE = re.compile(r"^\s*\|\s*source(\d*)_location\s*=\s*(.*)$", re.IGNORECASE)
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]]+))?")
COORD = re.compile(r"\{\{\s*coord\s*\|([^}]*)\}\}", re.IGNORECASE)
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
MAX_LINKS = 6


@dataclass(frozen=True)
class InfoboxSource:
    names: tuple[str, ...]
    items: tuple[str, ...]
    point: tuple[float, float] | None
    index: str = ""
    located: bool = False


@dataclass(frozen=True)
class InfoboxLinks:
    mouth: tuple[str, ...]
    sources: tuple[InfoboxSource, ...]


def degrees(parts: list[str]) -> float:
    return sum(float(part) / 60 ** index for index, part in enumerate(parts))


def coordinates(text: str) -> tuple[float, float] | None:
    if not (match := COORD.search(text)):
        return None
    parts = [part.strip() for part in match.group(1).split("|")]
    hemispheres = [index for index, part in enumerate(parts) if part.upper() in ("N", "S", "E", "W")]
    if len(hemispheres) >= 2:
        north, east = hemispheres[:2]
        lat, lon = degrees(parts[:north]), degrees(parts[north + 1:east])
        return (-lon if parts[east].upper() == "W" else lon, -lat if parts[north].upper() == "S" else lat)
    numbers = [float(part) for part in parts[:2] if NUMBER.match(part)]
    return (numbers[1], numbers[0]) if len(numbers) == 2 else None


class RiverInfobox:
    def __init__(self, session: requests.Session, language: str = "en"):
        self.session = session
        self.api = f"https://{language}.wikipedia.org/w/api.php"

    def links(self, article_title: str) -> InfoboxLinks:
        wikitext = self._get({"action": "parse", "page": article_title, "prop": "wikitext", "section": 0, "redirects": 1})["parse"]["wikitext"]
        lines = wikitext.splitlines()
        mouths = [title for line in lines if (match := MOUTH.match(line)) for title, _ in WIKILINK.findall(match.group(1))]
        points = {match.group(1): coordinates(match.group(2)) for line in lines if (match := SOURCE_COORDINATES.match(line))}
        places = {source.index: replace(source, located=True) for source in self._sources(lines, SOURCE_PLACE, points)}
        sources = [source if source.names else places.get(source.index, source) for source in self._sources(lines, SOURCE, points)]
        sources += [place for index, place in places.items() if index not in {source.index for source in sources}]
        return InfoboxLinks(tuple(item for title in mouths[:MAX_LINKS] if (item := self._item(title.strip()))), tuple(sources))

    def _sources(self, lines: list[str], pattern: re.Pattern, points: dict) -> list[InfoboxSource]:
        sources = []
        for line in lines:
            if match := pattern.match(line):
                links = WIKILINK.findall(match.group(2))[:MAX_LINKS]
                items = tuple(item for title, _ in links if (item := self._item(title.strip())))
                names = tuple((label or title).strip() for title, label in links)
                index = match.group(1) or ""
                sources.append(InfoboxSource(names, items, points.get(index), index))
        return sources

    def _item(self, title: str) -> str | None:
        pages = self._get({"action": "query", "titles": title, "prop": "pageprops", "ppprop": "wikibase_item", "redirects": 1})["query"]["pages"]
        return next((page["pageprops"]["wikibase_item"] for page in pages if "wikibase_item" in page.get("pageprops", {})), None)

    def _get(self, parameters: dict) -> dict:
        response = self.session.get(self.api, params={**parameters, "format": "json", "formatversion": 2}, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
