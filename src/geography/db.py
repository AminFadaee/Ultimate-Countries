import json
import pathlib
import sqlite3
from dataclasses import asdict, fields
from datetime import UTC, date, datetime

from geography.models import City, CityRole, Country, Field, Provenance
from geography.naming import slugify

SCHEMA = """
CREATE TABLE IF NOT EXISTS entity (
    id TEXT PRIMARY KEY,
    restcountries_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    official_name TEXT NOT NULL,
    alpha_2 TEXT,
    alpha_3 TEXT,
    ccn3 TEXT,
    wikipedia_url TEXT,
    sovereign INTEGER NOT NULL,
    disputed INTEGER NOT NULL,
    un_member INTEGER NOT NULL,
    un_observer INTEGER NOT NULL,
    dependency_type TEXT,
    parent_alpha_3 TEXT,
    region TEXT,
    subregion TEXT,
    area_km2 REAL,
    population INTEGER,
    demonym TEXT,
    demonym_female TEXT,
    government TEXT,
    government_form TEXT,
    income_group TEXT,
    government_spending REAL,
    military_spending REAL,
    economic_system TEXT,
    economic_system_disputed INTEGER NOT NULL DEFAULT 0,
    economic_system_disputed_with TEXT,
    flag TEXT,
    map TEXT
);

CREATE TABLE IF NOT EXISTS continent (
    entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    continent TEXT NOT NULL,
    PRIMARY KEY (entity_id, continent)
);

CREATE TABLE IF NOT EXISTS currency (
    entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    main INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entity_id, code)
);

CREATE TABLE IF NOT EXISTS other_demonym (
    entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    demonym TEXT NOT NULL,
    PRIMARY KEY (entity_id, rank)
);

CREATE TABLE IF NOT EXISTS language (
    entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    name TEXT NOT NULL,
    code TEXT,
    official TEXT,
    percent REAL,
    PRIMARY KEY (entity_id, rank)
);

CREATE TABLE IF NOT EXISTS official_religion (
    entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    religion TEXT NOT NULL,
    PRIMARY KEY (entity_id, religion)
);

CREATE TABLE IF NOT EXISTS religion (
    entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    religion TEXT NOT NULL,
    percent REAL NOT NULL,
    PRIMARY KEY (entity_id, rank)
);

CREATE TABLE IF NOT EXISTS membership (
    entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    organization TEXT NOT NULL,
    PRIMARY KEY (entity_id, organization)
);

CREATE TABLE IF NOT EXISTS founding (
    entity_id TEXT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
    established TEXT,
    independence TEXT,
    independence_from TEXT
);

CREATE TABLE IF NOT EXISTS city (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    population INTEGER,
    sitelinks INTEGER NOT NULL,
    osm_relation TEXT,
    map TEXT
);

CREATE TABLE IF NOT EXISTS entity_city (
    entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    city_id TEXT NOT NULL REFERENCES city(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    rank INTEGER NOT NULL,
    PRIMARY KEY (entity_id, city_id, role)
);

CREATE TABLE IF NOT EXISTS provenance (
    entity_id TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    source TEXT NOT NULL,
    as_of TEXT,
    retrieved TEXT NOT NULL,
    PRIMARY KEY (entity_id, field)
);
"""

SCALAR_COLUMNS = [
    "id",
    "restcountries_id",
    "name",
    "official_name",
    "alpha_2",
    "alpha_3",
    "ccn3",
    "wikipedia_url",
    "sovereign",
    "disputed",
    "un_member",
    "un_observer",
    "dependency_type",
    "parent_alpha_3",
    "region",
    "subregion",
    "area_km2",
    "population",
    "demonym",
    "demonym_female",
    "government",
    "government_form",
    "flag",
    "map",
]
ECONOMY_COLUMNS = [
    "income_group",
    "government_spending",
    "military_spending",
    "economic_system",
    "economic_system_disputed",
    "economic_system_disputed_with",
]
CITY_COLUMNS = [f.name for f in fields(City)]
BOOLEAN_COLUMNS = ("sovereign", "disputed", "un_member", "un_observer", "economic_system_disputed")
CHILD_TABLES = ("continent", "currency", "other_demonym", "language", "official_religion", "religion", "membership", "founding")
ROLE_FIELDS = {CityRole.CAPITAL: Field.CAPITALS, CityRole.NOTABLE: Field.NOTABLE_CITIES}


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def upsert(table: str, columns: list[str]) -> str:
    updates = ", ".join(f"{column} = excluded.{column}" for column in columns[1:])
    return f"""
        INSERT INTO {table} ({", ".join(columns)}) VALUES ({", ".join("?" for _ in columns)})
        ON CONFLICT (id) DO UPDATE SET {updates}
    """


def economy_values(country: Country) -> list:
    economy = country.economy
    if economy is None:
        return [None, None, None, None, 0, None]
    return [
        economy.income_group,
        economy.government_spending,
        economy.military_spending,
        economy.system,
        int(economy.disputed),
        economy.disputed_with,
    ]


class Database:
    def __init__(self, path: pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def remove_entities_except(self, entity_ids: set[str]) -> list[str]:
        stale = [row["id"] for row in self.connection.execute("SELECT id FROM entity") if row["id"] not in entity_ids]
        with self.connection:
            self.connection.executemany("DELETE FROM entity WHERE id = ?", [(entity_id,) for entity_id in stale])
        return stale

    def save_countries(self, countries: list[Country]) -> None:
        retrieved = today()
        with self.connection:
            for country in countries:
                self._save_country(country, retrieved)

    def retrieved(self, entity_id: str, field: Field) -> date | None:
        row = self.connection.execute(
            "SELECT retrieved FROM provenance WHERE entity_id = ? AND field = ?",
            (entity_id, field),
        ).fetchone()
        return date.fromisoformat(row["retrieved"]) if row else None

    def link_cities(self, entity_id: str, role: CityRole, city_ids: list[str], provenance: Provenance) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM entity_city WHERE entity_id = ? AND role = ?", (entity_id, role))
            self.connection.executemany(
                "INSERT OR IGNORE INTO city (id, name, sitelinks) VALUES (?, ?, 0)",
                [(city_id, city_id) for city_id in city_ids],
            )
            self.connection.executemany(
                "INSERT INTO entity_city VALUES (?, ?, ?, ?)",
                [(entity_id, city_id, role, rank) for rank, city_id in enumerate(city_ids)],
            )
            self._save_provenance(entity_id, ROLE_FIELDS[role], provenance, today())

    def linked_city_ids(self) -> set[str]:
        return {row["city_id"] for row in self.connection.execute("SELECT DISTINCT city_id FROM entity_city")}

    def save_cities(self, cities: list[City]) -> None:
        with self.connection:
            self.connection.executemany(
                upsert("city", CITY_COLUMNS),
                [tuple(asdict(city).values()) for city in cities],
            )
            self.connection.execute("DELETE FROM city WHERE id NOT IN (SELECT city_id FROM entity_city)")

    def city_map_targets(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT city.*, entity.id AS entity_id, entity.name AS entity_name, entity.alpha_3
            FROM city
            JOIN entity_city ON entity_city.city_id = city.id
            JOIN entity ON entity.id = entity_city.entity_id
            GROUP BY city.id
            ORDER BY entity.name, city.name
            """
        ).fetchall()

    def set_city_map(self, city_id: str, path: str) -> None:
        with self.connection:
            self.connection.execute("UPDATE city SET map = ? WHERE id = ?", (path, city_id))

    def set_entity_map(self, entity_id: str, path: str, provenance: Provenance) -> None:
        with self.connection:
            self.connection.execute("UPDATE entity SET map = ? WHERE id = ?", (path, entity_id))
            self._save_provenance(entity_id, Field.MAP, provenance, today())

    def export_json(self, directory: pathlib.Path) -> int:
        directory.mkdir(parents=True, exist_ok=True)
        entities = self.connection.execute("SELECT * FROM entity ORDER BY name").fetchall()
        documents = {slugify(entity["name"]): self._document(entity) for entity in entities}

        for stale in {path.stem for path in directory.glob("*.json")} - documents.keys():
            (directory / f"{stale}.json").unlink()
        for slug, document in documents.items():
            content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
            path = directory / f"{slug}.json"
            if not path.exists() or path.read_text() != content:
                path.write_text(content)
        return len(documents)

    def _save_country(self, country: Country, retrieved: str) -> None:
        values = [getattr(country, column) for column in SCALAR_COLUMNS]
        self.connection.execute(upsert("entity", SCALAR_COLUMNS + ECONOMY_COLUMNS), values + economy_values(country))
        for table in CHILD_TABLES:
            self.connection.execute(f"DELETE FROM {table} WHERE entity_id = ?", (country.id,))

        self.connection.executemany(
            "INSERT INTO continent VALUES (?, ?)",
            [(country.id, continent) for continent in dict.fromkeys(country.continents)],
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO currency VALUES (?, ?, ?, ?, ?)",
            [(country.id, c.code, c.name, c.symbol, c.main) for c in country.currencies],
        )
        self.connection.executemany(
            "INSERT INTO other_demonym VALUES (?, ?, ?)",
            [(country.id, rank, demonym) for rank, demonym in enumerate(country.other_demonyms)],
        )
        self.connection.executemany(
            "INSERT INTO language VALUES (?, ?, ?, ?, ?, ?)",
            [(country.id, rank, l.name, l.code, l.official, l.percent) for rank, l in enumerate(country.languages)],
        )
        self.connection.executemany(
            "INSERT INTO official_religion VALUES (?, ?)",
            [(country.id, religion) for religion in country.official_religions],
        )
        self.connection.executemany(
            "INSERT INTO religion VALUES (?, ?, ?, ?)",
            [(country.id, rank, r.name, r.percent) for rank, r in enumerate(country.religions)],
        )
        self.connection.executemany(
            "INSERT INTO membership VALUES (?, ?)",
            [(country.id, organization) for organization in country.memberships],
        )
        if country.established or country.independence:
            independence = country.independence
            self.connection.execute(
                "INSERT INTO founding VALUES (?, ?, ?, ?)",
                (
                    country.id,
                    country.established,
                    independence.date if independence else None,
                    independence.from_power if independence else None,
                ),
            )
        for field, provenance in country.provenance.items():
            self._save_provenance(country.id, field, provenance, retrieved)

    def _save_provenance(self, entity_id: str, field: Field, provenance: Provenance, retrieved: str) -> None:
        self.connection.execute(
            """
            INSERT INTO provenance VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (entity_id, field) DO UPDATE
            SET source = excluded.source, as_of = excluded.as_of, retrieved = excluded.retrieved
            """,
            (entity_id, field, provenance.source, provenance.as_of, retrieved),
        )

    def _rows(self, query: str, entity_id: str) -> list[dict]:
        return [dict(row) for row in self.connection.execute(query, (entity_id,))]

    def _document(self, entity: sqlite3.Row) -> dict:
        entity_id = entity["id"]
        row = dict(entity)
        for column in BOOLEAN_COLUMNS:
            row[column] = bool(row[column])
        cities = self._rows(
            """
            SELECT entity_city.role, city.id, city.name, city.latitude, city.longitude, city.population, city.map
            FROM entity_city JOIN city ON city.id = entity_city.city_id
            WHERE entity_city.entity_id = ?
            ORDER BY entity_city.role, entity_city.rank
            """,
            entity_id,
        )
        founding = next(iter(self._rows("SELECT * FROM founding WHERE entity_id = ?", entity_id)), {})
        return {
            "id": entity_id,
            "name": row["name"],
            "official_name": row["official_name"],
            "codes": {"alpha_2": row["alpha_2"], "alpha_3": row["alpha_3"], "ccn3": row["ccn3"]},
            "status": {
                key: row[key]
                for key in ("sovereign", "disputed", "un_member", "un_observer", "dependency_type", "parent_alpha_3")
            },
            "region": row["region"],
            "subregion": row["subregion"],
            "continents": [r["continent"] for r in self._rows("SELECT continent FROM continent WHERE entity_id = ?", entity_id)],
            "area_km2": row["area_km2"],
            "population": row["population"],
            "demonym": {
                "male": row["demonym"],
                "female": row["demonym_female"] or row["demonym"],
                "others": [r["demonym"] for r in self._rows("SELECT demonym FROM other_demonym WHERE entity_id = ? ORDER BY rank", entity_id)],
            },
            "capitals": [city["name"] for city in cities if city["role"] == CityRole.CAPITAL],
            "cities": cities,
            "currencies": [
                {**currency, "main": bool(currency["main"])}
                for currency in self._rows("SELECT code, name, symbol, main FROM currency WHERE entity_id = ? ORDER BY main DESC, code", entity_id)
            ],
            "languages": self._rows("SELECT name, code, official, percent FROM language WHERE entity_id = ? ORDER BY rank", entity_id),
            "official_religions": [r["religion"] for r in self._rows("SELECT religion FROM official_religion WHERE entity_id = ?", entity_id)],
            "religions": self._rows("SELECT religion, percent FROM religion WHERE entity_id = ? ORDER BY rank", entity_id),
            "government": {"label": row["government"], "form": row["government_form"]},
            "economy": {
                "system": row["economic_system"],
                "disputed": row["economic_system_disputed"],
                "disputed_with": row["economic_system_disputed_with"],
                "income_group": row["income_group"],
                "government_spending_pct_gdp": row["government_spending"],
                "military_spending_pct_gdp": row["military_spending"],
            },
            "established": founding.get("established"),
            "independence": (
                {"date": founding["independence"], "from": founding["independence_from"]}
                if founding.get("independence")
                else None
            ),
            "memberships": [r["organization"] for r in self._rows("SELECT organization FROM membership WHERE entity_id = ?", entity_id)],
            "flag": row["flag"],
            "map": row["map"],
            "wikipedia_url": row["wikipedia_url"],
            "sources": {
                r["field"]: {"source": r["source"], "as_of": r["as_of"]}
                for r in self._rows("SELECT field, source, as_of FROM provenance WHERE entity_id = ? ORDER BY field", entity_id)
            },
        }
