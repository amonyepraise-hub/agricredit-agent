-- AgriCredit Agent SQLite schema.
-- Every row produced by generate_synthetic_data.py is SYNTHETIC (see data/README.md).
-- The real-open-data mode (python -m agricredit.opendata build) fills the same tables, plus the
-- locations / commune_yields / weather_crosscheck / data_sources tables, from public sources.
PRAGMA foreign_keys = ON;

CREATE TABLE households (
    household_id TEXT PRIMARY KEY,
    location_id  TEXT NOT NULL,
    synthetic    INTEGER NOT NULL DEFAULT 1,
    notes        TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE plots (
    plot_id      TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(household_id),
    crop         TEXT NOT NULL,
    area_ha      REAL NOT NULL,
    soil_type    TEXT,
    location_id  TEXT NOT NULL
);
CREATE TABLE yield_history (
    record_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    plot_id         TEXT NOT NULL REFERENCES plots(plot_id),
    household_id    TEXT NOT NULL REFERENCES households(household_id),
    crop            TEXT NOT NULL,
    season          TEXT NOT NULL,
    yield_kg_per_ha REAL NOT NULL,
    recorded_at     TEXT NOT NULL
);
CREATE TABLE repayment_history (
    loan_id      TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(household_id),
    product      TEXT NOT NULL,
    amount_fcfa  REAL NOT NULL,
    due_date     TEXT NOT NULL,
    paid_date    TEXT,
    days_overdue INTEGER NOT NULL DEFAULT 0,
    defaulted    INTEGER NOT NULL DEFAULT 0,
    recorded_at  TEXT NOT NULL
);
CREATE TABLE current_obligations (
    obligation_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id            TEXT NOT NULL REFERENCES households(household_id),
    product                 TEXT NOT NULL,
    outstanding_amount_fcfa REAL NOT NULL,
    monthly_due_fcfa        REAL NOT NULL
);
CREATE TABLE weather_observations (
    observation_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id           TEXT NOT NULL,
    crop                  TEXT NOT NULL,
    season                TEXT NOT NULL,
    rainfall_mm           REAL NOT NULL,
    rainfall_anomaly_pct  REAL NOT NULL,
    seasonal_baseline_mm  REAL NOT NULL,
    drought_indicator     INTEGER NOT NULL DEFAULT 0,
    crop_calendar_signal  TEXT NOT NULL,
    observation_timestamp TEXT NOT NULL,
    retrieval_timestamp   TEXT NOT NULL,
    source_dataset        TEXT NOT NULL,
    source_url            TEXT                    -- set in real-open-data mode: where the observation came from
);
CREATE TABLE market_prices (
    price_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    commodity             TEXT NOT NULL,
    market_centre         TEXT NOT NULL,
    price_fcfa_per_kg     REAL NOT NULL,
    observation_timestamp TEXT NOT NULL,
    retrieval_timestamp   TEXT NOT NULL,
    source_dataset        TEXT NOT NULL,
    source_url            TEXT
);
CREATE TABLE input_packages (
    package_id                TEXT PRIMARY KEY,
    name                      TEXT NOT NULL,
    crop                      TEXT NOT NULL,
    cost_fcfa                 REAL NOT NULL,
    expected_yield_uplift_pct REAL NOT NULL DEFAULT 0
);
CREATE TABLE loan_products (
    product_id           TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    interest_rate_pct    REAL NOT NULL,
    fee_pct              REAL NOT NULL,
    typical_tenor_months INTEGER NOT NULL
);
-- A credit case: one household asking for one package on one product.
CREATE TABLE credit_cases (
    case_id               TEXT PRIMARY KEY,
    household_id          TEXT NOT NULL,          -- deliberately no FK: BEN-0009 references an unknown household
    package_id            TEXT NOT NULL REFERENCES input_packages(package_id),
    product_id            TEXT NOT NULL REFERENCES loan_products(product_id),
    crop                  TEXT NOT NULL,
    market_centre         TEXT NOT NULL,
    season                TEXT NOT NULL,
    requested_amount_fcfa REAL NOT NULL,
    tenor_months          INTEGER NOT NULL,
    scenario_tag          TEXT,                   -- documents which planted edge case this is (for evaluators)
    created_at            TEXT NOT NULL
);
-- Synthetic training labels for the risk model (see risk/train.py). Never shown to the agent.
CREATE TABLE synthetic_latent_traits (
    household_id TEXT PRIMARY KEY,
    reliability  REAL NOT NULL
);
-- Approvals and releases are written by the approval gate at run time.
CREATE TABLE approvals (
    approval_id         TEXT PRIMARY KEY,
    case_id             TEXT NOT NULL,
    recommendation_hash TEXT NOT NULL,
    officer_id          TEXT NOT NULL,
    officer_name        TEXT NOT NULL,
    decision            TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    reason              TEXT,
    model_version       TEXT NOT NULL,
    evidence_version    TEXT NOT NULL,
    consumed            INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE officers (
    officer_id   TEXT PRIMARY KEY,
    officer_name TEXT NOT NULL,
    role         TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE advisories (
    case_id             TEXT NOT NULL,
    recommendation_hash TEXT NOT NULL,
    advisory_json       TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (case_id, recommendation_hash)
);
CREATE TABLE release_outbox (
    release_id   TEXT PRIMARY KEY,
    case_id      TEXT NOT NULL,
    approval_id  TEXT NOT NULL UNIQUE,            -- one release per approval: replay protection
    payload_json TEXT NOT NULL,
    released_at  TEXT NOT NULL
);
-- Real-open-data mode only (empty in the synthetic store) ------------------------------------
CREATE TABLE locations (
    location_id TEXT PRIMARY KEY,          -- BEN-LOC-NN, one per commune
    fnid        TEXT NOT NULL UNIQUE,      -- FEWS NET / HarvestStat commune identifier
    name        TEXT NOT NULL,
    admin_1     TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    zone        TEXT NOT NULL
);
CREATE TABLE commune_yields (
    fnid            TEXT NOT NULL,
    crop            TEXT NOT NULL,
    harvest_year    INTEGER NOT NULL,
    area_ha         REAL NOT NULL,
    production_t    REAL NOT NULL,
    yield_kg_per_ha REAL NOT NULL,
    source_dataset  TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    PRIMARY KEY (fnid, crop, harvest_year)
);
CREATE TABLE weather_crosscheck (
    crosscheck_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id           TEXT NOT NULL,
    crop                  TEXT NOT NULL,
    season                TEXT NOT NULL,
    rainfall_mm           REAL NOT NULL,
    rainfall_anomaly_pct  REAL NOT NULL,
    seasonal_baseline_mm  REAL NOT NULL,
    observation_timestamp TEXT NOT NULL,
    source_dataset        TEXT NOT NULL,
    source_url            TEXT NOT NULL
);
CREATE TABLE data_sources (
    source_key   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    publisher    TEXT NOT NULL,
    licence      TEXT NOT NULL,
    url          TEXT NOT NULL,
    retrieved_at TEXT NOT NULL
);
CREATE INDEX idx_plots_hh ON plots(household_id);
CREATE INDEX idx_yield_hh ON yield_history(household_id);
CREATE INDEX idx_repay_hh ON repayment_history(household_id);
CREATE INDEX idx_weather ON weather_observations(location_id, crop, season);
CREATE INDEX idx_market ON market_prices(commodity, market_centre);
