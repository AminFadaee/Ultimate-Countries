import copy
import re
import urllib.parse
from dataclasses import dataclass

import requests
from lxml import html

from geography.dates import normalize_date
from geography.sources.http import TIMEOUT

API_URL = "https://en.wikipedia.org/w/api.php"
ARTICLE_URL = "https://en.wikipedia.org/wiki/"
FORMATION_PAGE = "List of modern sovereign states by date of formation"
COMMUNIST_STATES_PAGE = "Communist state"
CURRENT_COMMUNIST_STATES = "Current communist states"
FOOTNOTE = re.compile(r"\[[^\]]*\]")
SPACES = re.compile(r"\s+")

COUNTRY_COLUMN = "country"
ACQUIRED_COLUMN = "acquisition of full sovereignty"
SUBORDINATION_COLUMN = "date of last subordination"

INDEPENDENCE_PAGE = "List of national independence days"
EVENT_COLUMN = "event commemorated"
HOLIDAY_COLUMN = "date of holiday"
YEAR_COLUMN = "year of event"
GAINED_FROM_COLUMN = "independence gained from"
DAY_MONTH = re.compile(r"\b\d{1,2} [A-Z][a-z]+")

LANGUAGES_PAGE = "List of official languages by country and territory"
COUNT_COLUMN = "number of official (including de facto)"
OFFICIAL_COLUMN = "official language(s)"
REGIONAL_COLUMN = "regional language(s)"
LANGUAGES_OF = "Languages_of_"
DE_FACTO = re.compile(r"(\w[\w ]*?) has de facto status")
PARENTHESES = re.compile(r"\([^)]*\)")
ALIAS = re.compile(r"\(([A-Z][\w'-]*(?: [A-Z][\w'-]*)*)\)")
SEPARATORS = re.compile(r",| and ")

STATE_RELIGION_PAGE = "State religion"
STATE_RELIGION_SECTION = "Current states with a state religion"
RELIGION_SECTIONS = {"Buddhism", "Christianity", "Islam", "Judaism"}
DENOMINATIONS = {"Catholicism", "Eastern Orthodoxy", "Protestantism"}
MIXED_SUBSECTION = "Other/mixed"
RECOGNITION_ONLY = ("without establishing", "not recognized as a state religion", "holds special status")


class PageLayoutError(LookupError):
    pass


@dataclass(frozen=True)
class ListedLanguage:
    name: str
    aliases: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class FormationRecord:
    country_url: str
    acquired: str | None
    subordination_ended: str | None


@dataclass(frozen=True)
class IndependenceRecord:
    country_url: str
    event: str
    date: str | None
    from_power: str | None


@dataclass(frozen=True)
class LanguageStatus:
    country_url: str
    stated_count: int | None
    national: list[ListedLanguage]
    regional: list[ListedLanguage]

    @property
    def incomplete(self) -> bool:
        return self.stated_count is not None and len(self.national) < self.stated_count


@dataclass(frozen=True)
class StateReligion:
    country_url: str
    religion: str


def listed_language(part: str) -> ListedLanguage | None:
    name = PARENTHESES.sub("", part).strip()
    if not name or name[0].isdigit():
        return None
    return ListedLanguage(name, tuple(ALIAS.findall(part)))


def listed_languages(text: str | None) -> list[ListedLanguage]:
    if not text:
        return []
    de_facto = [ListedLanguage(match.group(1)) for match in DE_FACTO.finditer(text)]
    if text.casefold().startswith("none"):
        return de_facto
    parts = SEPARATORS.split(PARENTHESES.sub(strip_commas, text))
    return [language for language in map(listed_language, parts) if language] + de_facto


def stated_count(text: str | None) -> int | None:
    digits = re.match(r"\d+", text or "")
    return int(digits.group()) if digits else None


def strip_commas(match: re.Match) -> str:
    return match.group().replace(",", ";")


def clean(text: str) -> str | None:
    text = SPACES.sub(" ", FOOTNOTE.sub("", text)).strip(" ,;")
    return text or None


def cell_text(cell) -> str | None:
    cell = copy.deepcopy(cell)
    for element in cell.xpath(".//sup|.//style"):
        element.drop_tree()
    for br in cell.xpath(".//br"):
        br.tail = ", " + (br.tail or "")
    items = [clean(item.text_content()) for item in cell.xpath(".//li")]
    if items:
        return ", ".join(item for item in items if item)
    parts = [clean(text) for text in cell.xpath(".//text()")]
    return clean(" ".join(part for part in parts if part))


def linked_names(cell) -> str | None:
    names = [clean(link.text_content()) for link in cell.xpath(".//a[not(ancestor::sup)]")]
    names = [name for name in dict.fromkeys(names) if name]
    return ", ".join(names) if names else cell_text(cell)


def holiday_date(day_text: str | None, year_text: str | None) -> str | None:
    year = normalize_date(year_text)
    day = DAY_MONTH.search(day_text or "")
    if year and day and year.isdigit():
        return normalize_date(f"{day.group()} {year}") or year
    return year


def span_of(cell, attribute: str) -> int:
    digits = re.match(r"\d+", cell.get(attribute, "") or "")
    return max(1, int(digits.group())) if digits else 1


def expanded_rows(table) -> list[list]:
    pending = {}
    rows = []
    for row in table.xpath(".//tr"):
        queue = row.xpath("./th|./td")
        cells, column = [], 0
        while queue or column in pending:
            if column in pending:
                cell, remaining = pending.pop(column)
                if remaining > 1:
                    pending[column] = (cell, remaining - 1)
                cells.append(cell)
                column += 1
                continue
            cell = queue.pop(0)
            span = span_of(cell, "rowspan")
            for _ in range(span_of(cell, "colspan")):
                if span > 1:
                    pending[column] = (cell, span - 1)
                cells.append(cell)
                column += 1
        rows.append(cells)
    return rows


def article_url(href: str | None) -> str | None:
    if not href or not href.startswith("/wiki/") or ":" in href.removeprefix("/wiki/"):
        return None
    return ARTICLE_URL + urllib.parse.unquote(href.removeprefix("/wiki/")).split("#")[0]


def column_indexes(headers: list[str], names: tuple[str, ...], page: str) -> dict[str, int]:
    missing = [name for name in names if name not in headers]
    if missing:
        raise PageLayoutError(f"Columns {missing} not found on '{page}'")
    return {name: headers.index(name) for name in names}


def reaches(cells: list, column: dict[str, int]) -> bool:
    return len(cells) > max(column.values())


def required(records: list, page: str) -> list:
    if not records:
        raise PageLayoutError(f"No records parsed from '{page}'")
    return records


def first_link(cell) -> str | None:
    for href in cell.xpath(".//a/@href"):
        url = article_url(href)
        if url:
            return url
    return None


class Wikipedia:
    def __init__(self, session: requests.Session):
        self.session = session

    def formation(self) -> list[FormationRecord]:
        document = html.fromstring(self._page_html(FORMATION_PAGE))
        table = max(document.xpath("//table[contains(@class, 'wikitable')]"), key=lambda t: len(t.xpath(".//tr")))
        headers = [clean(th.text_content()).casefold() for th in table.xpath(".//tr[1]/th")]
        column = column_indexes(headers, (COUNTRY_COLUMN, ACQUIRED_COLUMN, SUBORDINATION_COLUMN), FORMATION_PAGE)

        records = []
        for cells in expanded_rows(table)[1:]:
            if len(cells) < len(headers):
                continue
            url = first_link(cells[column[COUNTRY_COLUMN]])
            if url is None:
                continue
            records.append(
                FormationRecord(
                    url,
                    acquired=normalize_date(cell_text(cells[column[ACQUIRED_COLUMN]])),
                    subordination_ended=normalize_date(cell_text(cells[column[SUBORDINATION_COLUMN]])),
                )
            )
        return required(records, FORMATION_PAGE)

    def independence_days(self) -> list[IndependenceRecord]:
        document = html.fromstring(self._page_html(INDEPENDENCE_PAGE))
        table = document.xpath("//table[contains(@class, 'wikitable')]")[0]
        rows = expanded_rows(table)
        headers = [(cell_text(cell) or "").casefold() for cell in rows[0]]
        names = (COUNTRY_COLUMN, EVENT_COLUMN, HOLIDAY_COLUMN, YEAR_COLUMN, GAINED_FROM_COLUMN)
        column = column_indexes(headers, names, INDEPENDENCE_PAGE)
        records = []
        for cells in rows[1:]:
            url = first_link(cells[column[COUNTRY_COLUMN]]) if reaches(cells, column) else None
            if url is None:
                continue
            records.append(
                IndependenceRecord(
                    url,
                    event=cell_text(cells[column[EVENT_COLUMN]]) or "",
                    date=holiday_date(cell_text(cells[column[HOLIDAY_COLUMN]]), cell_text(cells[column[YEAR_COLUMN]])),
                    from_power=linked_names(cells[column[GAINED_FROM_COLUMN]]),
                )
            )
        return required(records, INDEPENDENCE_PAGE)

    def language_statuses(self) -> list[LanguageStatus]:
        document = html.fromstring(self._page_html(LANGUAGES_PAGE))
        table = document.xpath("//table[contains(@class, 'wikitable')]")[0]
        rows = expanded_rows(table)
        headers = [(cell_text(cell) or "").casefold() for cell in rows[0]]
        column = column_indexes(headers, (COUNT_COLUMN, OFFICIAL_COLUMN, REGIONAL_COLUMN), LANGUAGES_PAGE)
        statuses = []
        for cells in rows[1:]:
            url = first_link(cells[0]) if reaches(cells, column) else None
            if url is None:
                continue
            country_url = url.replace(f"{ARTICLE_URL}{LANGUAGES_OF}", ARTICLE_URL)
            statuses.append(
                LanguageStatus(
                    country_url,
                    stated_count(cell_text(cells[column[COUNT_COLUMN]])),
                    listed_languages(cell_text(cells[column[OFFICIAL_COLUMN]])),
                    listed_languages(cell_text(cells[column[REGIONAL_COLUMN]])),
                )
            )
        return required(statuses, LANGUAGES_PAGE)

    def state_religions(self) -> list[StateReligion]:
        document = html.fromstring(self._page_html(STATE_RELIGION_PAGE))
        religions, religion, denomination = [], None, None
        inside, recognition_only, sections = False, False, set()
        for element in document.iter("h2", "h3", "h4", "p", "li"):
            text = clean(element.text_content()) or ""
            if element.tag == "h2":
                inside = text == STATE_RELIGION_SECTION
            elif not inside:
                continue
            elif element.tag == "h3":
                religion, denomination, recognition_only = (text if text in RELIGION_SECTIONS else None), None, False
                sections.add(religion)
            elif element.tag == "h4":
                denomination = text if text in DENOMINATIONS else None
                recognition_only = text == MIXED_SUBSECTION
            elif element.tag == "p":
                recognition_only = recognition_only or any(phrase in text.casefold() for phrase in RECOGNITION_ONLY)
            elif religion and not recognition_only and element.getparent().getparent().tag != "li":
                url = first_link(element)
                if url:
                    religions.append(StateReligion(url, denomination or religion))
        missing = RELIGION_SECTIONS - sections
        if missing:
            raise PageLayoutError(f"Sections {sorted(missing)} not found on '{STATE_RELIGION_PAGE}'")
        return required(religions, STATE_RELIGION_PAGE)

    def communist_states(self) -> list[str]:
        document = html.fromstring(self._page_html(COMMUNIST_STATES_PAGE))
        for header in document.xpath("//th"):
            if clean(header.text_content()) == CURRENT_COMMUNIST_STATES:
                row = header.getparent().getnext()
                return [url for url in (article_url(href) for href in row.xpath(".//a/@href")) if url]
        raise PageLayoutError(f"'{CURRENT_COMMUNIST_STATES}' not found on '{COMMUNIST_STATES_PAGE}'")

    def _page_html(self, page: str) -> str:
        response = self.session.get(
            API_URL,
            params={"action": "parse", "page": page, "prop": "text", "format": "json", "formatversion": 2, "redirects": 1},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["parse"]["text"]
