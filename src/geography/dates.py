import re
from datetime import datetime

MONTHS = "Jan|Feb|Mar|Apr|May|June?|July?|Aug|Sept?|Oct|Nov|Dec"
DAY_MONTH_YEAR = re.compile(rf"\b(\d{{1,2}}) ({MONTHS})[a-z]*\.? (\d{{3,4}})\b")
MONTH_DAY_YEAR = re.compile(rf"\b({MONTHS})[a-z]*\.? (\d{{1,2}}), (\d{{3,4}})\b")
YEAR = re.compile(r"\b(\d{3,4})\b")
APPROXIMATE = re.compile(r"\bc\.|\bcirca\b", re.IGNORECASE)


def month_number(name: str) -> int:
    return datetime.strptime(name[:3], "%b").month


def normalize_date(text: str | None) -> str | None:
    if not text:
        return None
    if match := DAY_MONTH_YEAR.search(text):
        day, month, year = match.groups()
        return f"{int(year):04d}-{month_number(month):02d}-{int(day):02d}"
    if match := MONTH_DAY_YEAR.search(text):
        month, day, year = match.groups()
        return f"{int(year):04d}-{month_number(month):02d}-{int(day):02d}"
    if match := YEAR.search(text):
        prefix = "c. " if APPROXIMATE.search(text) else ""
        return f"{prefix}{int(match.group(1))}"
    return None
