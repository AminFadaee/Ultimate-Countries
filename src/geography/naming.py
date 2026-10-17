import pathlib
import unicodedata


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(c if c.isalnum() else "_" for c in ascii_name.casefold()).strip("_")


def answer_path(path: str | pathlib.Path) -> str | pathlib.Path:
    original = pathlib.PurePath(path)
    return type(path)(original.with_name(f"{original.stem}-answer{original.suffix}"))
