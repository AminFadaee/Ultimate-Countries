import re
from enum import StrEnum

FEDERAL = "federal"
WORD = re.compile(r"[\w-]+")


class Executive(StrEnum):
    DIRECTORIAL = "directorial"
    SEMI_PRESIDENTIAL = "semi-presidential"
    PRESIDENTIAL = "presidential"
    PARLIAMENTARY = "parliamentary"


class Monarchy(StrEnum):
    ABSOLUTE = "absolute"
    SEMI_CONSTITUTIONAL = "semi-constitutional"
    CONSTITUTIONAL = "constitutional"


COMMUNIST_STATE = "communist state"
ISLAMIC_REPUBLIC = "islamic republic"
EMIRATE = "emirate"
CO_PRINCIPALITY = "co-principality"
MONARCHY = "monarchy"
REPUBLIC = "republic"


def words_of(label: str) -> set[str]:
    return set(WORD.findall(label.casefold()))


def first_present(options: type[StrEnum], words: set[str]) -> StrEnum | None:
    return next((option for option in options if option.value in words), None)


def kind_of(label: str, words: set[str]) -> str | None:
    text = label.casefold()
    if COMMUNIST_STATE in text:
        return "communist state"
    if EMIRATE in words:
        return "Islamic emirate"
    if CO_PRINCIPALITY in words:
        return "parliamentary co-principality"
    if MONARCHY in words:
        monarchy = first_present(Monarchy, words)
        return f"{monarchy.value} monarchy" if monarchy else MONARCHY
    if ISLAMIC_REPUBLIC in text:
        return "Islamic republic"
    if REPUBLIC in words:
        executive = first_present(Executive, words)
        return f"{executive.value} republic" if executive else REPUBLIC
    return None


def government_form(label: str | None) -> str | None:
    if not label:
        return None
    words = words_of(label)
    kind = kind_of(label, words)
    if kind is None:
        return None
    form = f"{FEDERAL} {kind}" if FEDERAL in words else kind
    return form[0].upper() + form[1:]
