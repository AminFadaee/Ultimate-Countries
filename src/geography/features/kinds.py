from dataclasses import dataclass
from enum import StrEnum

from geography.render import Style


class Shape(StrEnum):
    AREA = "area"
    LINE = "line"
    POINT = "point"
    WORLD_AREA = "world area"
    WORLD_LINE = "world line"


@dataclass(frozen=True)
class Palette:
    outline: str
    fill: str

    def style(self, shape: Shape) -> Style:
        if shape is Shape.POINT:
            return Style(highlight=self.outline, outline=self.outline)
        return Style(highlight=self.outline, host=self.fill, outline=self.outline)


MOUNTAINS = Palette("#5A3719", "#F6F3EE")
VOLCANIC = Palette("#7A1F0B", "#F6F3EE")
DRY = Palette("#D35400", "#F8CB9E")
LOWLAND = Palette("#2E7D32", "#C3E2BC")
HIGHLAND = Palette("#827717", "#E6E3A1")
WET = Palette("#00838F", "#B2EBF2")
FOREST = Palette("#1B5E20", "#A5D6A7")
LAND = Palette("#A6192E", "#E8C3B9")
CONTINENTAL = Palette("#00796B", "#B6E0DA")
WATER = Palette("#0D47A1", "#6FA8DC")
FLOW = Palette("#0B3C8C", "#0B3C8C")
ROCK = Palette("#8B4513", "#F0C9A8")
LINES = Palette("#6A1B9A", "#6A1B9A")
CORAL = Palette("#D84315", "#FFCCBC")


@dataclass(frozen=True)
class KindSpec:
    palette: Palette
    shape: Shape
    photo_card: bool = False


class Kind(StrEnum):
    CONTINENT = "Continent"
    OCEAN = "Ocean"
    SEA = "Sea"
    LAKE = "Lake"
    RIVER = "River"
    WATERFALL = "Waterfall"
    MOUNTAIN_RANGE = "Mountain range"
    MOUNTAIN = "Mountain"
    VOLCANO = "Volcano"
    DESERT = "Desert"
    PLATEAU = "Plateau"
    PLAIN = "Plain"
    BASIN = "Basin"
    VALLEY = "Valley"
    DELTA = "Delta"
    WETLAND = "Wetland"
    REEF = "Reef"
    PENINSULA = "Peninsula"
    ISTHMUS = "Isthmus"
    REGION = "Region"
    RAINFOREST = "Rainforest"
    CANYON = "Canyon"
    LANDMARK = "Landmark"
    CANAL = "Canal"
    LINE = "Line"

    @property
    def spec(self) -> KindSpec:
        return SPECS[self]

    @property
    def tag(self) -> str:
        return self.value.replace(" ", "_")


SPECS = {
    Kind.CONTINENT: KindSpec(CONTINENTAL, Shape.AREA),
    Kind.OCEAN: KindSpec(WATER, Shape.WORLD_AREA),
    Kind.SEA: KindSpec(WATER, Shape.AREA),
    Kind.LAKE: KindSpec(WATER, Shape.AREA),
    Kind.RIVER: KindSpec(FLOW, Shape.LINE),
    Kind.WATERFALL: KindSpec(WATER, Shape.POINT, photo_card=True),
    Kind.MOUNTAIN_RANGE: KindSpec(MOUNTAINS, Shape.AREA),
    Kind.MOUNTAIN: KindSpec(MOUNTAINS, Shape.POINT, photo_card=True),
    Kind.VOLCANO: KindSpec(VOLCANIC, Shape.POINT, photo_card=True),
    Kind.DESERT: KindSpec(DRY, Shape.AREA),
    Kind.PLATEAU: KindSpec(HIGHLAND, Shape.AREA),
    Kind.PLAIN: KindSpec(LOWLAND, Shape.AREA),
    Kind.BASIN: KindSpec(LOWLAND, Shape.AREA),
    Kind.VALLEY: KindSpec(LOWLAND, Shape.AREA),
    Kind.DELTA: KindSpec(WET, Shape.AREA),
    Kind.WETLAND: KindSpec(WET, Shape.AREA),
    Kind.REEF: KindSpec(CORAL, Shape.AREA, photo_card=True),
    Kind.PENINSULA: KindSpec(LAND, Shape.AREA),
    Kind.ISTHMUS: KindSpec(LAND, Shape.AREA),
    Kind.REGION: KindSpec(LAND, Shape.AREA),
    Kind.RAINFOREST: KindSpec(FOREST, Shape.AREA, photo_card=True),
    Kind.CANYON: KindSpec(ROCK, Shape.AREA, photo_card=True),
    Kind.LANDMARK: KindSpec(LAND, Shape.POINT, photo_card=True),
    Kind.CANAL: KindSpec(FLOW, Shape.LINE, photo_card=True),
    Kind.LINE: KindSpec(LINES, Shape.WORLD_LINE),
}
