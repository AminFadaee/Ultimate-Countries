import re
from difflib import SequenceMatcher

GENERIC = frozenset({
    "mount", "mountain", "mountains", "lake", "river", "national", "park", "island", "islands", "sea", "canal", "polder", "gulf", "strait", "plain",
    "plateau", "desert", "waterfall", "falls", "peninsula", "delta", "valley", "range", "the", "of", "de", "van", "and", "bay", "reservoir", "kuh", "kooh",
    "dasht", "wildlife", "refuge", "reserve",
})
MIN_LENGTH = 4
PREFIX = 5
SIMILARITY = 0.8
ALIAS = re.compile(r"___(?:,)? (?:or|also (?:spelled|spelt|known as|called|written)) (?:[A-Z][\w'-]*)(?: [A-Z][\w'-]*)*")
WORD = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


def name_parts(name: str) -> list[str]:
    return [part.casefold() for part in re.findall(r"[^\W\d_]{3,}", name, re.UNICODE) if part.casefold() not in GENERIC]


def reveals(word: str, parts: list[str]) -> bool:
    folded = word.casefold()
    if folded in GENERIC or len(folded) < MIN_LENGTH:
        return False
    return any(
        folded.startswith(part[:PREFIX]) or part.startswith(folded[:PREFIX]) or SequenceMatcher(None, folded, part).ratio() >= SIMILARITY
        for part in parts
        if len(part) >= MIN_LENGTH
    )


def masked(document: dict) -> str:
    text = document.get("description") or ""
    parts = name_parts(document["name"])
    if not text or not parts:
        return ""
    text = WORD.sub(lambda match: "___" if reveals(match.group(), parts) else match.group(), text)
    return re.sub(r"(___[ -]?)+", "___ ", ALIAS.sub("___", text)).replace("___ ,", "___,").replace("___ .", "___.").strip()
