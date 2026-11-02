import re
import unicodedata
from enum import StrEnum

RIVER_LIKE = {"river", "stream", "brook", "creek", "watercourse", "tributary"}
GLACIER = {"glacier", "ice field", "ice cap", "icefield", "mountain glacier", "valley glacier"}
WETLAND = {"swamp", "marsh", "bog", "raised bog", "wetland", "floodplain lake", "flooded grasslands and savannas", "fen", "mire"}
CAVE = {"cave", "show cave", "karst cave", "limestone cave", "cave system", "sea cave", "ice cave", "lava tube"}
DAM = {"dam", "embankment dam", "gravity dam", "arch dam", "arch-gravity dam", "buttress dam", "rockfill dam"}
RESERVOIR = {"reservoir", "artificial lake"}
SPRING = {"spring", "river source", "karst spring", "hot spring", "thermal spring", "mineral spring", "headwater"}
HIGHLAND = {
    "mountain range", "mountain", "plateau", "highland", "upland", "hill", "massif", "volcano", "mountain system", "hills",
    "mountain pass", "pass", "mountain chain", "mountain ridge", "ridge", "peak", "mountain peak", "summit", "mountain group", "mountains",
}
CITY = {"city", "big city", "metropolis", "megacity", "capital", "national capital", "federal capital", "largest city"}
VILLAGE = {"village", "hamlet", "rural locality", "village in the united states", "settlement"}
MARINE = {
    "sea", "ocean", "gulf", "bay", "strait", "marginal sea", "adjacent sea", "mediterranean sea", "inlet", "estuary", "fjord", "lagoon",
    "sound", "channel", "sea inlet", "bight", "cove", "firth", "ria",
}
WATER_BODY = {"lake", "endorheic lake", "salt lake", "sea", "reservoir", "former lake"}
WETTED_LAND = {"delta", "wetland", "inland delta", "marsh", "swamp"}
AREA_WORDS = ("basin", "desert", "region", "former")
DRIED_WORDS = ("former", "dry", "dried", "extinct", "vanished", "drained", "desiccated")
CATEGORIES = (
    "cave", "show cave", "reservoir", "dam", "glacier", "ice field", "ice cap", "spring", "swamp", "marsh", "raised bog", "bog", "wetland",
    "floodplain lake", "lake", "mountain range", "plateau", "highland", "mountain",
)
DESCRIPTION = re.compile(r"\b(source|sources|spring|springs|headwater|headwaters|origin)\b|quelle")


class SourceKind(StrEnum):
    GLACIER = "glacier"
    CAVE = "cave"
    DAM = "dam"
    HIGHLAND = "highland"
    WETLAND = "wetland"
    SPRING = "spring"
    CITY = "city"
    COMMUNE = "commune"
    VILLAGE = "village"
    TOWN = "town"
    LAKE = "lake"
    UNKNOWN = "unknown"

    @property
    def is_place(self) -> bool:
        return self in (SourceKind.CITY, SourceKind.COMMUNE, SourceKind.VILLAGE, SourceKind.TOWN)

    @property
    def has_icon(self) -> bool:
        return self not in (SourceKind.HIGHLAND, SourceKind.GLACIER, SourceKind.LAKE, SourceKind.UNKNOWN)


class MouthKind(StrEnum):
    SEA = "sea"
    RIVER = "river"
    LAKE = "lake"
    DRIED = "dried"
    DELTA = "delta"
    LAND = "land"


def folded(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()


def settlement_kind(classes: set[str]) -> SourceKind | None:
    if classes & CITY:
        return SourceKind.CITY
    if any("commune" in name or name.startswith(("municipality", "municipal")) for name in classes):
        return SourceKind.COMMUNE
    if classes & VILLAGE or any(name.startswith("village") for name in classes):
        return SourceKind.VILLAGE
    if any("town" in name or "urban-type settlement" in name for name in classes):
        return SourceKind.TOWN
    return None


def source_kind(classes: set[str], is_lake: bool) -> SourceKind:
    ordered = (
        (GLACIER, SourceKind.GLACIER), (CAVE, SourceKind.CAVE), (DAM, SourceKind.DAM), (HIGHLAND, SourceKind.HIGHLAND),
        (WETLAND, SourceKind.WETLAND), (SPRING, SourceKind.SPRING),
    )
    for names, kind in ordered:
        if classes & names:
            return kind
    if settlement := settlement_kind(classes):
        return settlement
    return SourceKind.LAKE if is_lake or classes & RESERVOIR else SourceKind.UNKNOWN


def source_label(name: str, classes: set[str]) -> str:
    if not DESCRIPTION.search(folded(name)):
        return name
    category = next((category for category in CATEGORIES if category in classes), "spring" if classes & SPRING else None)
    return category.capitalize() if category else name
