"""
Database layer for India Household Price Intelligence.
Raw sqlite3 (no ORM) for speed and transparency.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "hpi.db")
DB_PATH = os.path.abspath(DB_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    tier TEXT,               -- 'metro' or 'telangana_regional'
    UNIQUE(city, state)
);

CREATE TABLE IF NOT EXISTS product_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_type TEXT,          -- 'government', 'retail', 'manual'
    connector_name TEXT,
    update_frequency TEXT,
    quality_grade_default TEXT,  -- A/B/C/D/E
    active_status INTEGER DEFAULT 1,
    last_run_at TEXT,
    last_status TEXT,
    records_ingested INTEGER DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    basket_name TEXT NOT NULL,   -- 'essential' or 'actual'
    basket_type TEXT,
    product_id INTEGER NOT NULL,
    weight REAL NOT NULL,
    purchase_frequency REAL,     -- purchases per month, proxy for frequency
    active_status INTEGER DEFAULT 1,
    FOREIGN KEY(product_id) REFERENCES product_master(id)
);

CREATE TABLE IF NOT EXISTS weekly_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    run_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT,
    records_ingested INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS insight_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,        -- movement, materiality, breadth, persistence, evidence
    weight REAL NOT NULL,
    sample_size INTEGER DEFAULT 0,
    hit_rate REAL,
    last_recalibrated TEXT,
    UNIQUE(metric_name)
);

CREATE TABLE IF NOT EXISTS external_drivers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_round TEXT NOT NULL,        -- e.g. '2023-24'
    geography TEXT NOT NULL,           -- 'rural' | 'urban'
    item_group TEXT NOT NULL,          -- e.g. 'food_total', 'cereals', 'edible_oil'
    mpce_rs REAL,                      -- average MPCE for this item group, rupees/month
    share_pct REAL,                    -- % share of total MPCE
    source_url TEXT,
    UNIQUE(survey_round, geography, item_group)
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    # migration for databases created before the grade column existed
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
    print(f"Initialized DB at {DB_PATH}")
