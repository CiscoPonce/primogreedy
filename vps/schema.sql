-- DuckDB schema for PrimoGreedy data layer
-- Run: duckdb data.duckdb < schema.sql

-- Seen tickers — replaces seen_tickers.json
CREATE TABLE IF NOT EXISTS seen_tickers (
    ticker    VARCHAR NOT NULL,
    region    VARCHAR DEFAULT 'USA',
    seen_at   TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ticker)
);

-- Paper portfolio — replaces paper_portfolio.json
CREATE TABLE IF NOT EXISTS paper_portfolio (
    id            INTEGER PRIMARY KEY DEFAULT nextval('portfolio_seq'),
    ticker        VARCHAR NOT NULL,
    entry_price   DOUBLE NOT NULL,
    date          DATE NOT NULL,
    verdict       VARCHAR NOT NULL,
    source        VARCHAR DEFAULT 'unknown',
    position_size DOUBLE DEFAULT 0,
    order_id      VARCHAR,
    fill_price    DOUBLE,
    broker_status VARCHAR DEFAULT 'none',
    analysis_id   INTEGER,           -- link to the accepted analysis (review gate)
    created_at    TIMESTAMP DEFAULT current_timestamp,
    UNIQUE (ticker, date)  -- prevent duplicate same-day entries
);

CREATE SEQUENCE IF NOT EXISTS portfolio_seq START 1;

-- Agent run log — operational metrics for LangSmith correlation
CREATE TABLE IF NOT EXISTS agent_runs (
    id          VARCHAR PRIMARY KEY,
    ticker      VARCHAR NOT NULL,
    timestamp   TIMESTAMP DEFAULT current_timestamp,
    status      VARCHAR NOT NULL,  -- PASS / FAIL
    model       VARCHAR,
    latency_ms  INTEGER,
    region      VARCHAR DEFAULT 'USA'
);

-- Structured investment analyses (proposal.md 3-layer framework)
CREATE SEQUENCE IF NOT EXISTS analysis_seq START 1;

CREATE TABLE IF NOT EXISTS analyses (
    id                INTEGER PRIMARY KEY DEFAULT nextval('analysis_seq'),
    ticker            VARCHAR NOT NULL,
    analyzed_at       TIMESTAMP DEFAULT current_timestamp,
    stage             VARCHAR NOT NULL,   -- RESEARCH | WATCHLIST | BUY_CANDIDATE | DISCARD
    verdict           VARCHAR,            -- STRONG BUY | BUY | WATCH | AVOID (legacy label)
    source            VARCHAR DEFAULT 'unknown',
    price             DOUBLE,
    currency          VARCHAR DEFAULT 'USD',
    business_summary       TEXT,
    financial_evolution    TEXT,
    cash_flow              TEXT,
    debt_liquidity         TEXT,
    dilution               TEXT,
    valuation_method       TEXT,
    valuation_bear         TEXT,
    valuation_base         TEXT,
    valuation_bull         TEXT,
    valuation_range_low    DOUBLE,
    valuation_range_high   DOUBLE,
    margin_of_safety       TEXT,
    risks                  TEXT,
    invalidation_facts     TEXT,
    macro_factors          TEXT,
    portfolio_fit          TEXT,
    sources                TEXT,   -- JSON: [{type,url,date,period}]
    data_quality           TEXT,   -- JSON: {facts,estimates,inferences,unverified,pending}
    proposed_action        VARCHAR, -- PENDING|INITIATE|MAINTAIN|INCREASE|REDUCE|CLOSE|DISCARD
    review_trigger         TEXT,
    next_review_at         DATE,
    report_text            TEXT,
    position_size          DOUBLE DEFAULT 0,
    created_at             TIMESTAMP DEFAULT current_timestamp
);

-- Watchlist funnel: research stage -> accept/dismiss decision gate
CREATE TABLE IF NOT EXISTS watchlist (
    ticker      VARCHAR NOT NULL PRIMARY KEY,
    stage       VARCHAR NOT NULL,
    source      VARCHAR DEFAULT 'unknown',
    analysis_id INTEGER,
    reason      TEXT,
    added_at    TIMESTAMP DEFAULT current_timestamp,
    status      VARCHAR DEFAULT 'active'   -- active | accepted | dismissed
);
