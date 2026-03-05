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
