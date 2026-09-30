from dataclasses import dataclass, field

import geopandas as gpd

from geography.features.kinds import Kind
from geography.features.spatial import OurCountry


@dataclass(frozen=True)
class Photo:
    file: str
    source: str


@dataclass
class Feature:
    key: str
    name: str
    kind: Kind
    geometry: gpd.GeoSeries
    sitelinks: int = 0
    qid: str | None = None
    wikipedia: str | None = None
    image: str | None = None
    countries: list[OurCountry] = field(default_factory=list)
    city: str | None = None
    height_m: float | None = None
    elevation_m: float | None = None
    length_km: float | None = None
    area_km2: float | None = None
    built: str | None = None
    culture: str | None = None
    world_heritage: bool = False
    photo: Photo | None = None
    map: str | None = None

    @property
    def continents(self) -> list[str]:
        return sorted({country.continent for country in self.countries if country.continent})

    @property
    def tags(self) -> list[str]:
        tags = [self.kind.tag, *(continent.replace(" ", "_") for continent in self.continents)]
        if self.world_heritage:
            tags.append("World_Heritage")
        return tags
