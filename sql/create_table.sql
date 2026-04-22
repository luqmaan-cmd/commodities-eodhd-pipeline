-- EODHD Commodities Pipeline
-- DDL for the commodities_eod table

CREATE TABLE IF NOT EXISTS commodities_eod (
    date            DATE            NOT NULL,
    symbol          VARCHAR(20)     NOT NULL,
    name            VARCHAR(100)    NOT NULL,
    open            NUMERIC,
    high            NUMERIC,
    low             NUMERIC,
    close           NUMERIC,
    adjusted_close  NUMERIC,
    volume          BIGINT,
    ingestion_ts    TIMESTAMP       DEFAULT NOW(),
    PRIMARY KEY (date, symbol)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_commodities_eod_symbol ON commodities_eod (symbol);
CREATE INDEX IF NOT EXISTS idx_commodities_eod_date ON commodities_eod (date);

-- ──────────────────────────────────────────────────────────────────────────────
-- Rejected rows table — stores rows that failed validation for audit/reprocessing
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commodities_eod_rejected (
    id              BIGSERIAL        PRIMARY KEY,
    date            DATE             NOT NULL,
    symbol          VARCHAR(20)      NOT NULL,
    name            VARCHAR(100)     NOT NULL,
    open            NUMERIC,
    high            NUMERIC,
    low             NUMERIC,
    close           NUMERIC,
    adjusted_close  NUMERIC,
    volume          BIGINT,
    rejected_reason TEXT             NOT NULL,
    rejected_at     TIMESTAMP        DEFAULT NOW()
);

-- Indexes for auditing rejected rows
CREATE INDEX IF NOT EXISTS idx_rejected_symbol ON commodities_eod_rejected (symbol);
CREATE INDEX IF NOT EXISTS idx_rejected_date ON commodities_eod_rejected (date);
CREATE INDEX IF NOT EXISTS idx_rejected_at ON commodities_eod_rejected (rejected_at);
