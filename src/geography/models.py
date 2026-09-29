from dataclasses import dataclass, field
from enum import StrEnum


class Field(StrEnum):
    IDENTITY = "identity"
    POPULATION = "population"
    DEMONYM = "demonym"
    CURRENCIES = "currencies"
    GOVERNMENT = "government"
    GOVERNMENT_FORM = "government_form"
    MEMBERSHIPS = "memberships"
    REGION = "region"
    OFFICIAL_LANGUAGES = "official_languages"
    SPOKEN_LANGUAGES = "spoken_languages"
    OFFICIAL_RELIGIONS = "official_religions"
    RELIGIONS = "religions"
    ESTABLISHED = "established"
    INDEPENDENCE = "independence"
    RECOGNITION = "recognition"
    INCOME_GROUP = "income_group"
    GOVERNMENT_SPENDING = "government_spending"
    MILITARY_SPENDING = "military_spending"
    ECONOMIC_SYSTEM = "economic_system"
    CAPITALS = "capitals"
    NOTABLE_CITIES = "notable_cities"
    FLAG = "flag"
    MAP = "map"


class Source(StrEnum):
    RESTCOUNTRIES = "restcountries"
    WIKIDATA = "wikidata"
    WIKIPEDIA = "wikipedia"
    PEW = "pew"
    CLDR = "cldr"
    WORLD_BANK = "world_bank"
    IMF = "imf"
    NATURAL_EARTH = "natural_earth"
    DERIVED = "derived"


class OfficialStatus(StrEnum):
    NATIONAL = "national"
    REGIONAL = "regional"


class EconomicSystem(StrEnum):
    MARKET = "Market economy"
    WELFARE = "Market economy with extensive welfare state"
    SOCIALIST_MARKET = "Socialist market economy"
    PLANNED = "Socialist planned economy"


class CityRole(StrEnum):
    CAPITAL = "capital"
    NOTABLE = "notable"


@dataclass(frozen=True)
class Provenance:
    source: Source
    as_of: str | None = None


@dataclass(frozen=True)
class Currency:
    code: str
    name: str
    symbol: str
    main: bool = False


@dataclass(frozen=True)
class Language:
    name: str
    code: str | None
    official: OfficialStatus | None
    percent: float | None


@dataclass(frozen=True)
class Religion:
    name: str
    percent: float


@dataclass(frozen=True)
class Independence:
    date: str | None
    from_power: str | None


@dataclass(frozen=True)
class Recognition:
    limited: bool
    recognised_count: int | None
    recognised_by: list[str]
    claimants: list[str]


@dataclass(frozen=True)
class ListedCapital:
    name: str
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class Economy:
    system: EconomicSystem | None
    disputed: bool
    disputed_with: EconomicSystem | None
    income_group: str | None
    government_spending: float | None
    military_spending: float | None


@dataclass
class Country:
    id: str
    restcountries_id: str
    name: str
    official_name: str
    alpha_2: str | None
    alpha_3: str | None
    ccn3: str | None
    wikipedia_url: str | None
    sovereign: bool
    disputed: bool
    un_member: bool
    un_observer: bool
    dependency_type: str | None
    parent_alpha_3: str | None
    region: str | None
    subregion: str | None
    area_km2: float | None
    population: int | None
    demonym: str | None
    demonym_female: str | None
    government: str | None
    government_form: str | None = None
    other_demonyms: list[str] = field(default_factory=list)
    listed_capitals: list[ListedCapital] = field(default_factory=list)
    continents: list[str] = field(default_factory=list)
    currencies: list[Currency] = field(default_factory=list)
    memberships: list[str] = field(default_factory=list)
    languages: list[Language] = field(default_factory=list)
    official_religions: list[str] = field(default_factory=list)
    religions: list[Religion] = field(default_factory=list)
    established: str | None = None
    independence: Independence | None = None
    recognition: Recognition | None = None
    economy: Economy | None = None
    flag: str | None = None
    map: str | None = None
    provenance: dict[Field, Provenance] = field(default_factory=dict)


@dataclass(frozen=True)
class City:
    id: str
    name: str
    latitude: float | None
    longitude: float | None
    population: int | None
    sitelinks: int
    osm_relation: str | None
