"""
Database layer for India Household Price Intelligence.

Dual-mode: SQLite locally (no DATABASE_URL set -- the original, unchanged
behavior), or Postgres (Neon) in production when DATABASE_URL is set. This
matters because Render's free-tier disk is NOT persistent across
redeploys/restarts -- verified directly this session: every redeploy wiped
the database and re-seeded from scratch, silently discarding any real data
(AGMARKNET pulls, manually-added CSVs) since the last deploy. Postgres on
Neon is a genuinely separate, persistent service, so a Render redeploy can
no longer touch the data.

The rest of the codebase (connectors, app.py, metrics.py, intelligence.py,
etc.) was written entirely with SQLite's `?` placeholder style and relies on
dict-like row access (sqlite3.Row) and `cursor.lastrowid`. Rather than touch
every query in every file, PGConnWrapper/PGCursorWrapper below translate
transparently: `?` -> `%s`, and INSERT statements get an automatic
`RETURNING id` appended so `.lastrowid` keeps working. This is a deliberate
compatibility-shim choice, not an accident -- it keeps the diff small and
the existing, already-tested query logic untouched.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "hpi.db")
DB_PATH = os.path.abspath(DB_PATH)

DATABASE_URL = os.environ.get("DATABASE_URL")

# Two schema variants: SQLite's AUTOINCREMENT vs Postgres's SERIAL, and
# SQLite's `INTEGER` vs Postgres's more precise types where it matters.
# Kept as one shared template with a placeholder swapped in, rather than
# two fully separate copies, so schema changes only need to happen once.
_PK_SQLITE = "INTEGER PRIMARY KEY AUTOINCREMENT"
_PK_POSTGRES = "SERIAL PRIMARY KEY"

_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS locations (
    id {PK},
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    tier TEXT,               -- 'metro' or 'telangana_regional'
    UNIQUE(city, state)
);

CREATE TABLE IF NOT EXISTS product_master (
    id {PK},
    category TEXT NOT NULL,
    commodity TEXT NOT NULL,
    sub_category TEXT,
    brand TEXT,
    variant TEXT,
    pack_quantity REAL,
    pack_unit TEXT,
    standard_unit TEXT,      -- 'kg' or 'litre' or '100g'
    branded_flag INTEGER DEFAULT 0,
    essential_flag INTEGER DEFAULT 1,
    regional_relevance TEXT, -- e.g. 'telangana' or 'national'
    structural_flag INTEGER DEFAULT 1,  -- 1 = structural, 0 = volatile
    active_status INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS data_sources (
    id {PK},
    source_name TEXT NOT NULL,
    source_type TEXT,          -- 'government', 'retail', 'manual'
    connector_name TEXT,
    update_frequency TEXT,
    quality_grade_default TEXT,  -- A/B/C/D/E
    active_status INTEGER DEFAULT 1,
    last_run_at TEXT,
    last_status TEXT,
    records_ingested INTEGER DEFAULT 0,
    last_error TEXT,
    UNIQUE(source_name)
);

CREATE TABLE IF NOT EXISTS price_observations (
    id {PK},
    observation_date TEXT NOT NULL,   -- YYYY-MM-DD
    product_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    channel TEXT NOT NULL,   -- mandi, official, local_retail, supermarket, quick_commerce
    grade TEXT DEFAULT 'FAQ',   -- FAQ (Fair Average Quality, the AGMARKNET/Consumer Affairs default) or a lower grade like 'Non-FAQ'
    arrivals REAL,   -- mandi arrivals volume (tonnes), mandi channel only -- AGMARKNET reports this field
    original_quantity REAL,
    original_unit TEXT,
    original_price REAL,
    standard_quantity REAL,
    standard_unit TEXT,
    normalized_unit_price REAL NOT NULL,
    provisional_flag INTEGER DEFAULT 0,
    data_quality_score TEXT,   -- A/B/C/D/E
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(product_id) REFERENCES product_master(id),
    FOREIGN KEY(location_id) REFERENCES locations(id),
    FOREIGN KEY(source_id) REFERENCES data_sources(id)
);
CREATE INDEX IF NOT EXISTS idx_obs_lookup ON price_observations(product_id, location_id, channel, observation_date);
CREATE INDEX IF NOT EXISTS idx_obs_grade ON price_observations(product_id, location_id, channel, grade, observation_date);

CREATE TABLE IF NOT EXISTS inflation_benchmarks (
    id {PK},
    benchmark_type TEXT NOT NULL,  -- 'CPI', 'CPI_FOOD', 'WPI'
    commodity_group TEXT,
    state TEXT,
    month INTEGER,
    year INTEGER,
    value REAL,
    mom_change REAL,
    yoy_change REAL,
    status TEXT,   -- provisional/final
    source_id INTEGER  -- links back to data_sources; without this the
                        -- Sources page confidence scoring has no way to
                        -- attribute rows here to a specific connector
);

CREATE TABLE IF NOT EXISTS basket_definitions (
    id {PK},
    basket_name TEXT NOT NULL,   -- 'essential' or 'actual'
    basket_type TEXT,
    product_id INTEGER NOT NULL,
    weight REAL NOT NULL,
    purchase_frequency REAL,     -- purchases per month, proxy for frequency
    active_status INTEGER DEFAULT 1,
    FOREIGN KEY(product_id) REFERENCES product_master(id)
);

CREATE TABLE IF NOT EXISTS weekly_insights (
    id {PK},
    week_start TEXT,
    week_end TEXT,
    insight_type TEXT,      -- Regular / Shock / Unseen
    commodity TEXT,
    city TEXT,
    title TEXT,
    summary TEXT,
    wow_change REAL,
    mom_change REAL,
    yoy_change REAL,
    wholesale_change REAL,
    retail_change REAL,
    quick_commerce_change REAL,
    movement_score REAL,
    materiality_score REAL,
    breadth_score REAL,
    persistence_score REAL,
    evidence_score REAL,
    household_impact_score REAL,
    signal_confidence TEXT,   -- High/Medium/Low
    causal_confidence TEXT,   -- Confirmed/Likely/Possible/Insufficient Evidence
    likely_driver TEXT,
    insight_classification TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ingestion_logs (
    id {PK},
    source_id INTEGER,
    run_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT,
    records_ingested INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS insight_log (
    id {PK},
    logged_at TEXT NOT NULL,          -- the "as of" date the insight was generated for
    product_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    commodity TEXT,
    city TEXT,
    classification TEXT,              -- Regular / Shock
    composite_score REAL,
    wow_pct_at_log_time REAL,
    price_at_log_time REAL,
    outcome_checked INTEGER DEFAULT 0,
    outcome_pct_change_14d REAL,       -- actual pct change 14 days after logging
    outcome_persisted INTEGER,         -- 1 if the move held up (same direction, >=half magnitude), 0 if not
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_insight_log_lookup ON insight_log(product_id, location_id, logged_at);

CREATE TABLE IF NOT EXISTS scoring_weights (
    id {PK},
    metric_name TEXT NOT NULL,        -- movement, materiality, breadth, persistence, evidence
    weight REAL NOT NULL,
    sample_size INTEGER DEFAULT 0,
    hit_rate REAL,
    last_recalibrated TEXT,
    UNIQUE(metric_name)
);

CREATE TABLE IF NOT EXISTS external_drivers (
    id {PK},
    driver_type TEXT NOT NULL,        -- e.g. 'diesel_price', 'rainfall_mm'
    scope TEXT,                       -- e.g. 'national', 'Telangana'
    observation_date TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    source_id INTEGER,
    data_quality_score TEXT,
    UNIQUE(driver_type, scope, observation_date)
);
CREATE INDEX IF NOT EXISTS idx_driver_lookup ON external_drivers(driver_type, scope, observation_date);

CREATE TABLE IF NOT EXISTS consumption_hces (
    id {PK},
    survey_round TEXT NOT NULL,        -- e.g. '2023-24'
    geography TEXT NOT NULL,           -- 'rural' | 'urban'
    item_group TEXT NOT NULL,          -- e.g. 'food_total', 'cereals', 'edible_oil'
    mpce_rs REAL,                      -- average MPCE for this item group, rupees/month
    share_pct REAL,                    -- % share of total MPCE
    source_url TEXT,
    UNIQUE(survey_round, geography, item_group)
);
"""


def _get_sqlite_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class PGCursorWrapper:
    """Makes a psycopg2 cursor look enough like a sqlite3 cursor that none
    of the existing query code (written entirely against sqlite3) needs to
    change: translates `?` placeholders to `%s`, and auto-appends
    `RETURNING id` to plain INSERTs so `.lastrowid` keeps working the same
    way it did under sqlite3."""

    def __init__(self, real_cursor):
        self._cur = real_cursor
        self._last_inserted_id = None

    def execute(self, sql, params=()):
        translated = sql.replace("?", "%s")
        stripped = translated.strip().upper()
        if stripped.startswith("INSERT") and "RETURNING" not in stripped:
            translated = translated.rstrip().rstrip(";") + " RETURNING id"
            try:
                self._cur.execute(translated, params)
                row = self._cur.fetchone()
                self._last_inserted_id = row["id"] if row else None
            except Exception:
                # Table might not have an `id` column (shouldn't happen here,
                # every table does) -- fall back to a plain insert rather
                # than silently eating a real error.
                self._cur.execute(sql.replace("?", "%s"), params)
                self._last_inserted_id = None
        else:
            self._cur.execute(translated, params)
        return self

    def executemany(self, sql, seq_of_params):
        """CRITICAL FIX: psycopg2's own executemany() does NOT batch at the
        network level despite the name -- it silently issues one round trip
        per row, identical in cost to calling execute() in a loop. This was
        caught directly this session: seed.py's 898,808-row bulk insert
        would have taken an estimated many hours (each round trip to Neon
        from India carries real network latency) before this fix, instead
        of the couple of minutes real batching takes.

        psycopg2.extras.execute_values() is the actual batching mechanism:
        it rewrites many single-row VALUES tuples into large multi-row
        INSERT statements, cutting round trips by roughly the page_size
        factor. This requires rewriting the `? , ? , ?...` single-row
        template into the `VALUES %s` form execute_values expects -- done
        here via a regex split on the query's own VALUES(...) clause, so
        the calling code (seed.py, drivers_seed.py, etc.) never has to
        know or care that this rewrite is happening."""
        import re
        translated = sql.replace("?", "%s")
        match = re.search(r"(.*VALUES\s*)(\([^()]*\))(\s*(?:ON CONFLICT.*)?)$", translated, re.IGNORECASE | re.DOTALL)
        if not match:
            # Fallback: couldn't parse the shape safely, use the correct
            # (if slower) executemany rather than silently mis-batching.
            self._cur.executemany(translated, seq_of_params)
            return self
        prefix, row_template, suffix = match.groups()
        from psycopg2.extras import execute_values
        execute_values(self._cur, f"{prefix}%s{suffix}", seq_of_params, template=row_template, page_size=1000)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return self._last_inserted_id


class PGConnWrapper:
    """Same idea as PGCursorWrapper, at the connection level: exposes
    .execute() directly (sqlite3 connections support this; raw psycopg2
    connections don't), and .executescript() for running the multi-statement
    schema in one call."""

    def __init__(self, url):
        import psycopg2
        import psycopg2.extras
        self._conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor, connect_timeout=15)

    def cursor(self):
        return PGCursorWrapper(self._conn.cursor())

    def execute(self, sql, params=()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, sql):
        # psycopg2's execute() (unlike sqlite3's) natively handles multiple
        # semicolon-separated statements in one call.
        cur = self._conn.cursor()
        cur.execute(sql)
        self._conn.commit()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_conn():
    if DATABASE_URL:
        return PGConnWrapper(DATABASE_URL)
    return _get_sqlite_conn()


def init_db():
    conn = get_conn()
    if DATABASE_URL:
        schema = _SCHEMA_TEMPLATE.replace("{PK}", _PK_POSTGRES)
        conn.executescript(schema)
    else:
        schema = _SCHEMA_TEMPLATE.replace("{PK}", _PK_SQLITE)
        conn.executescript(schema)
        # migration for SQLite databases created before the grade column
        # existed -- Postgres deployments are always fresh, so this branch
        # only matters for the local SQLite path.
        try:
            conn.execute("ALTER TABLE price_observations ADD COLUMN grade TEXT DEFAULT 'FAQ'")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("UPDATE price_observations SET grade='FAQ' WHERE grade IS NULL")
        try:
            conn.execute("ALTER TABLE price_observations ADD COLUMN arrivals REAL")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    if DATABASE_URL:
        print("Initialized Postgres schema via DATABASE_URL")
    else:
        print(f"Initialized DB at {DB_PATH}")
