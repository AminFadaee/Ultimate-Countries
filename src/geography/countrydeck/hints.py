import re
from difflib import SequenceMatcher

GENERIC = frozenset({
    "mount", "mountain", "mountains", "lake", "river", "national", "park", "island", "islands", "sea", "canal", "polder", "gulf", "strait", "plain",
    "plateau", "desert", "waterfall", "falls", "peninsula", "delta", "valley", "range", "the", "of", "de", "van", "and", "bay", "reservoir", "kuh", "kooh",
    "dasht", "wildlife", "refuge", "reserve", "palace", "palaces", "airport", "museum", "church", "cathedral", "mosque", "castle", "bridge", "dam", "station",
    "stadium", "tower", "garden", "gardens", "gate", "caravanserai", "caravanserais", "inscription", "observatory", "shrine", "tomb", "fort", "fortress",
    "wall", "house", "square", "monastery", "monasteries", "complex", "city", "village", "windmills", "works", "port", "harbour", "bazaar",
})
MIN_LENGTH = 4
PREFIX = 5
SIMILARITY = 0.8
ALIAS = re.compile(r"___(?:,)? (?:or|also (?:spelled|spelt|known as|called|written)) (?:[A-Z][\w'-]*)(?: [A-Z][\w'-]*)*")
OLD_NAME = re.compile(r"___, (?:the )?(?:ancient|old|historic|classical|medieval) [A-Z][\w'-]*(?: [A-Z][\w'-]*)*,")
SUBJECT = re.compile(r"^(?P<subject>.{1,160}?)\s+(?=(?:is|are|was|were|lies|stands|covers|flows|has|forms|consists)\b)")
SUBJECT_WORDS = 25
REPEATED = re.compile(r"___(?:,? (?:or |and )?___)+")
WORD = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


def name_parts(*names: str | None) -> list[str]:
    parts = (part.casefold() for name in names if name for part in re.findall(r"[^\W\d_]{3,}", name, re.UNICODE))
    return list(dict.fromkeys(part for part in parts if part not in GENERIC))


def reveals(word: str, parts: list[str]) -> bool:
    folded = word.casefold()
    return any(resembles(piece, parts) for piece in dict.fromkeys([folded, *re.split(r"[-']", folded)]) if piece)


def resembles(folded: str, parts: list[str]) -> bool:
    if folded in GENERIC:
        return False
    if folded in parts:
        return True
    if len(folded) < MIN_LENGTH:
        return False
    return any(
        folded.startswith(part[:PREFIX]) or part.startswith(folded[:PREFIX]) or SequenceMatcher(None, folded, part).ratio() >= SIMILARITY
        for part in parts
        if len(part) >= MIN_LENGTH
    )


def masked(document: dict) -> str:
    text = document.get("description") or ""
    parts = name_parts(document["name"], document.get("native"))
    if not text or not parts:
        return ""
    text = WORD.sub(lambda match: "___" if reveals(match.group(), parts) else match.group(), text)
    text = OLD_NAME.sub("___,", ALIAS.sub("___", text))
    text = SUBJECT.sub(lambda match: "___ " if len(match["subject"].split()) <= SUBJECT_WORDS else match.group(), text, count=1)
    return REPEATED.sub("___", re.sub(r"(___[ -]?)+", "___ ", text)).replace("___ ,", "___,").replace("___ .", "___.").strip()
