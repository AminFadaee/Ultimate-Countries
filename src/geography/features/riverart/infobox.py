import re

import requests

from geography.sources.http import TIMEOUT
from geography.sources.wikipedia import API_URL

PARAMETERS = {"mouth": re.compile(r"^\|\s*mouth\s*=\s*(.*)$", re.IGNORECASE), "source": re.compile(r"^\|\s*source\d*\s*=\s*(.*)$", re.IGNORECASE)}
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
MAX_LINKS = 6


class RiverInfobox:
    def __init__(self, session: requests.Session):
        self.session = session

    def links(self, article_title: str) -> dict[str, list[str]]:
        wikitext = self._get({"action": "parse", "page": article_title, "prop": "wikitext", "section": 0})["parse"]["wikitext"]
        found = {}
        for parameter, pattern in PARAMETERS.items():
            titles = [title.strip() for line in wikitext.splitlines() if (match := pattern.match(line)) for title in WIKILINK.findall(match.group(1))]
            found[parameter] = [item for title in titles[:MAX_LINKS] if (item := self._item(title))]
        return found

    def _item(self, title: str) -> str | None:
        pages = self._get({"action": "query", "titles": title, "prop": "pageprops", "ppprop": "wikibase_item", "redirects": 1})["query"]["pages"]
        return next((page["pageprops"]["wikibase_item"] for page in pages if "wikibase_item" in page.get("pageprops", {})), None)

    def _get(self, parameters: dict) -> dict:
        response = self.session.get(API_URL, params={**parameters, "format": "json", "formatversion": 2}, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
