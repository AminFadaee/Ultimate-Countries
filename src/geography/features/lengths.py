import re

KM_PER_MILE = 1.609344
TOLERANCE = 0.05
MIN_LENGTH_KM = 5
NUMBER = r"(?P<value>\d[\d,]*(?:\.\d+)?)"
UNIT = r"(?P<unit>kilomet(?:re|er)s?|km|miles?|mi)\b"
VERB = r"(?:flows|runs|stretches|extends|extending|spans|measures|is)\s+(?:for\s+)?(?:about|roughly|approximately|some|nearly|over|more than)?\s*"
BEFORE = re.compile(VERB + NUMBER + r"[- ]?" + UNIT, re.IGNORECASE)
AFTER = re.compile(NUMBER + r"[- ]?" + UNIT + r"(?:\s*\([^)]*\))?[- ]?(?:long\b|in (?:total )?length)", re.IGNORECASE)


def kilometres(match: re.Match) -> float:
    value = float(match["value"].replace(",", ""))
    return value * KM_PER_MILE if match["unit"].lower().startswith(("mile", "mi")) else value


def stated_length_km(text: str) -> float | None:
    for pattern in (AFTER, BEFORE):
        match = pattern.search(text)
        if match and kilometres(match) > MIN_LENGTH_KM:
            return kilometres(match)
    return None


def disagrees(length_km: float, description: str) -> bool:
    stated = stated_length_km(description)
    return stated is not None and abs(stated - length_km) / length_km > TOLERANCE
