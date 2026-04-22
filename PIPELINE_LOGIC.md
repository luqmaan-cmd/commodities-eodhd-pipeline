# EODHD Commodities Pipeline — Logic Document

## Overview

A Python pipeline that ingests commodity price data from the EODHD API into a PostgreSQL database. The pipeline has two modes: a **one-time backfill** of all historical data, and a **daily ingestion** job that fetches each new day's data.

---

## Data Source

- **API**: EODHD End-of-Day Historical Data API
- **Exchange Code**: `COMM`
- **Symbol Format**: `{CODE}.COMM` (e.g., `GC.COMM` for Gold)
- **Base URL**: `https://eodhistoricaldata.com/api/eod/{SYMBOL}?api_token={TOKEN}&fmt=json`
- **Optional Params**: `from=YYYY-MM-DD`, `to=YYYY-MM-DD`, `period=d|w|m`
- **Update Schedule**: EODHD updates commodities ~2-3 hours after CME close (~7-8 PM EST)
- **Weekends**: No updates — markets are closed

---

## Commodity Symbol List

42 unique commodities, defined centrally in `config/commodities.yaml`:

| # | Symbol | Name | API Code | Category |
|---|--------|------|----------|----------|
| 1 | CL | Crude Oil (WTI) | CL.COMM | Energy |
| 2 | BZ | Brent Crude | BZ.COMM | Energy |
| 3 | NG | Natural Gas | NG.COMM | Energy |
| 4 | HO | Heating Oil | HO.COMM | Energy |
| 5 | RB | RBOB Gasoline | RB.COMM | Energy |
| 6 | EH | Ethanol | EH.COMM | Energy |
| 7 | NCFY | Newcastle Coal | NCFY.COMM | Energy |
| 8 | GC | Gold (COMEX) | GC.COMM | Precious Metals |
| 9 | SI | Silver | SI.COMM | Precious Metals |
| 10 | PL | Platinum | PL.COMM | Precious Metals |
| 11 | PA | Palladium | PA.COMM | Precious Metals |
| 12 | HG | Copper | HG.COMM | Industrial Metals |
| 13 | ALI | Aluminum (COMEX) | ALI.COMM | Industrial Metals |
| 14 | NICKEL | Nickel | NICKEL.COMM | Industrial Metals |
| 15 | HRC | Hot-Rolled Coil Steel | HRC.COMM | Industrial Metals |
| 16 | TIO | Iron Ore 62% Fe | TIO.COMM | Industrial Metals |
| 17 | ZC | Corn | ZC.COMM | Grains |
| 18 | ZS | Soybean | ZS.COMM | Grains |
| 19 | SM | Soybean Meal | SM.COMM | Grains |
| 20 | ZL | Soybean Oil | ZL.COMM | Grains |
| 21 | W | Wheat (CBOT) | W.COMM | Grains |
| 22 | KE | KC Hard Red Winter Wheat | KE.COMM | Grains |
| 23 | EBM | Milling Wheat N2 | EBM.COMM | Grains |
| 24 | EMA | Maize (Paris) | EMA.COMM | Grains |
| 25 | O | Oats | O.COMM | Grains |
| 26 | ZR | Rough Rice | ZR.COMM | Grains |
| 27 | KC | Coffee | KC.COMM | Softs |
| 28 | CC | Cocoa | CC.COMM | Softs |
| 29 | SB | Sugar | SB.COMM | Softs |
| 30 | CT | Cotton | CT.COMM | Softs |
| 31 | OJ | Orange Juice | OJ.COMM | Softs |
| 32 | RU | Rubber | RU.COMM | Softs |
| 33 | CPO | Palm Oil | CPO.COMM | Softs |
| 34 | LE | Live Cattle | LE.COMM | Livestock & Dairy |
| 35 | FC | Feeder Cattle | FC.COMM | Livestock & Dairy |
| 36 | HE | Lean Hogs | HE.COMM | Livestock & Dairy |
| 37 | DC | Class III Milk | DC.COMM | Livestock & Dairy |
| 38 | GDK | Class IV Milk | GDK.COMM | Livestock & Dairy |
| 39 | CB | Cash-settled Butter | CB.COMM | Livestock & Dairy |
| 40 | DY | Dry Whey | DY.COMM | Livestock & Dairy |
| 41 | LBR | Lumber | LBR.COMM | Other |
| 42 | LGOc3 | Gas Oil | LGOc3.COMM | Other |

---

## Database Schema

### Table: `commodities_eod`

```sql
CREATE TABLE commodities_eod (
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
```

### Indexes

```sql
CREATE INDEX idx_commodities_eod_symbol ON commodities_eod (symbol);
CREATE INDEX idx_commodities_eod_date ON commodities_eod (date);
```

---

## Pipeline Modes

### Mode 1: Backfill (one-time)

**Purpose**: Load all historical data for all 42 commodities.

**Logic**:
1. Read commodity list from `config/commodities.yaml`
2. For each commodity:
   a. Call EODHD API with no date range → returns full history
   b. Validate each row of the response
   c. Standardize the data (rename fields, add metadata)
   d. Upsert into `commodities_eod` table
3. Log results per commodity and a summary at the end
4. If a commodity fails, log the error and continue to the next
5. Track which symbols succeeded/failed for resumability

**API Calls**: 42 (one per commodity)

**Idempotency**: `INSERT ... ON CONFLICT (date, symbol) DO UPDATE` — safe to re-run

### Mode 2: Daily Ingestion

**Purpose**: Fetch yesterday's data for all 42 commodities.

**Logic**:
1. Determine the target date (yesterday, or a date passed via CLI arg)
2. Read commodity list from `config/commodities.yaml`
3. For each commodity:
   a. Call EODHD API with `from={target_date}&to={target_date}`
   b. Validate the response
   c. Standardize the data
   d. Upsert into `commodities_eod` table
4. Log results per commodity and a summary at the end
5. Run post-ingestion validation checks
6. Alert on any failures

**API Calls**: 42 (one per commodity)

**Schedule**: Cloud Scheduler at 9 PM EST, Mon-Fri

**Idempotency**: Same as backfill — `ON CONFLICT DO UPDATE`

---

## API Client Logic

### Request Flow

```
For each commodity:
  1. Build URL: https://eodhistoricaldata.com/api/eod/{api_code}?api_token={TOKEN}&fmt=json
  2. Add date params if daily mode: &from={date}&to={date}
  3. Send GET request
  4. Check HTTP status:
     - 200 → parse JSON response
     - 429 → rate limited → wait and retry
     - 5xx → server error → retry with exponential backoff
     - 404 → symbol not found → log error, skip this commodity
     - Other → log error, skip this commodity
  5. Parse JSON array of OHLCV objects
  6. Return list of standardized rows
```

### Retry Logic

- **Max retries**: 3
- **Backoff**: Exponential (1s, 2s, 4s)
- **Retry on**: HTTP 429, 5xx, timeout, connection error
- **Do not retry on**: HTTP 404, 401, 403

### Rate Limiting

- **Delay between requests**: 0.5 seconds (to stay well within 100,000 calls/day)
- 42 calls × 1 per day = 42 calls — trivial usage

---

## Data Standardization

### Raw EODHD Response

```json
[
  {
    "date": "2025-04-01",
    "open": 3129.7,
    "high": 3149.5,
    "low": 3104.0,
    "close": 3118.9,
    "adjusted_close": 3118.9,
    "volume": 1721
  }
]
```

### Standardized Row

| Field | Source | Type |
|-------|--------|------|
| `date` | `date` | DATE |
| `symbol` | Config `symbol` | VARCHAR(20) |
| `name` | Config `name` | VARCHAR(100) |
| `open` | `open` | NUMERIC |
| `high` | `high` | NUMERIC |
| `low` | `low` | NUMERIC |
| `close` | `close` | NUMERIC |
| `adjusted_close` | `adjusted_close` | NUMERIC |
| `volume` | `volume` | BIGINT |
| `ingestion_ts` | `NOW()` | TIMESTAMP |

**Key point**: The `symbol` and `name` come from our config, NOT from the API response. The API response only has price data.

---

## Data Validation

### Per-Row Checks

| # | Check | Rule | Action |
|---|-------|------|--------|
| 1 | Null prices | `open`, `high`, `low`, `close` must not be null | Reject row, log warning |
| 2 | High >= Low | `high` must be >= `low` | Reject row, log warning |
| 3 | High >= Open/Close | `high` must be >= `open` AND `close` | Accept row, log warning |
| 4 | Low <= Open/Close | `low` must be <= `open` AND `close` | Accept row, log warning |
| 5 | Volume >= 0 | `volume` must be non-negative | Reject row, log warning |
| 6 | Price anomaly | Daily % change > 30% vs previous close | Accept row, log warning for review |

### Post-Ingestion Checks

| # | Check | Rule | Action |
|---|-------|------|--------|
| 7 | Row count | Expected 42 rows for the target date | Alert if count != 42 |
| 8 | Data freshness | Latest date in table should be yesterday (or target date) | Alert if stale |
| 9 | Missing symbols | All 42 symbols should have data for the target date | Alert on missing |
| 10 | Null check | No null values in required fields after insert | Alert if found |

---

## Idempotency

All writes use PostgreSQL `INSERT ... ON CONFLICT`:

```sql
INSERT INTO commodities_eod (date, symbol, name, open, high, low, close, adjusted_close, volume, ingestion_ts)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
ON CONFLICT (date, symbol)
DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    adjusted_close = EXCLUDED.adjusted_close,
    volume = EXCLUDED.volume,
    name = EXCLUDED.name,
    ingestion_ts = EXCLUDED.ingestion_ts;
```

This guarantees:
- Re-running the pipeline for the same date produces the same result
- No duplicate rows
- Backfill can be re-run safely
- Failed runs can be re-run without manual cleanup

---

## Alerting

| Alert | Trigger | Channel |
|-------|---------|---------|
| Pipeline failure | Cloud Run job exits non-zero | Slack / Email |
| Validation failures | Any row rejected | Slack (warning) |
| Missing commodities | < 42 rows ingested for target date | Slack (warning) |
| Stale data | Latest date in table > 1 day behind | Slack / Email |
| API errors | Repeated API failures after retries | Slack / Email |

Implementation: Cloud Monitoring alerts → Pub/Sub → Slack webhook / email

---

## Logging

### Per-Commodity Log

```json
{
  "timestamp": "2026-04-22T21:00:00Z",
  "pipeline_type": "daily",
  "target_date": "2026-04-21",
  "symbol": "GC",
  "status": "success",
  "rows_fetched": 1,
  "rows_valid": 1,
  "rows_rejected": 0,
  "api_response_time_ms": 340,
  "validation_warnings": [],
  "error": null
}
```

### Run Summary Log

```json
{
  "timestamp": "2026-04-22T21:01:00Z",
  "pipeline_type": "daily",
  "target_date": "2026-04-21",
  "total_symbols": 42,
  "successful": 41,
  "failed": 1,
  "failed_symbols": ["OJ"],
  "total_rows_upserted": 41,
  "total_rows_rejected": 0,
  "run_duration_seconds": 28
}
```

---

## Project Structure

```
eodhd-commodities/
├── config/
│   └── commodities.yaml          # Symbol list, categories
├── src/
│   ├── __init__.py
│   ├── api_client.py             # EODHD API calls + retry logic
│   ├── models.py                 # Standardized data model
│   ├── validator.py              # Data validation checks
│   ├── db.py                    # PostgreSQL connection + upsert logic
│   ├── alerter.py               # Alerting (Slack/email)
│   ├── logger.py                # Structured logging setup
│   └── pipeline.py              # Orchestrator (backfill + daily)
├── sql/
│   └── create_table.sql         # DDL for commodities_eod
├── jobs/
│   ├── backfill.py              # Backfill entry point
│   └── daily.py                 # Daily ingestion entry point
├── tests/
│   ├── test_validator.py
│   ├── test_api_client.py
│   └── test_pipeline.py
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## Tech Stack

| Component | Choice |
|-----------|--------|
| Language | Python |
| Database | PostgreSQL |
| DB Driver | psycopg2 |
| Compute | Cloud Run Job |
| Scheduler | Cloud Scheduler |
| Alerting | Cloud Monitoring + Slack webhook |
| Logging | Cloud Logging (structured JSON) |
| Config | YAML (commodities.yaml) |

---

## GCP Setup (gcloud commands)

### Prerequisites

- GCP project already exists
- `gcloud` CLI installed and authenticated
- PostgreSQL instance running (Cloud SQL or external)

### 1. Enable Required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com
```

### 2. Store Secrets in Secret Manager

```bash
echo -n "<EODHD_API_KEY>" | gcloud secrets create eodhd-api-key --data-file=-
echo -n "<DB_CONNECTION_STRING>" | gcloud secrets create commodities-db-url --data-file=-
echo -n "<SLACK_WEBHOOK_URL>" | gcloud secrets create slack-webhook-url --data-file=-
```

### 3. Build & Push Docker Image

```bash
gcloud builds submit --tag gcr.io/<PROJECT_ID>/eodhd-commodities
```

### 4. Create Cloud Run Job (Backfill)

```bash
gcloud run jobs create commodities-backfill \
  --image gcr.io/<PROJECT_ID>/eodhd-commodities \
  --task-count 1 \
  --max-retries 0 \
  --set-env-vars "PIPELINE_MODE=backfill" \
  --set-secrets "EODHD_API_KEY=eodhd-api-key:latest,DB_URL=commodities-db-url:latest,SLACK_WEBHOOK_URL=slack-webhook-url:latest" \
  --region <REGION>
```

### 5. Create Cloud Run Job (Daily)

```bash
gcloud run jobs create commodities-daily \
  --image gcr.io/<PROJECT_ID>/eodhd-commodities \
  --task-count 1 \
  --max-retries 2 \
  --set-env-vars "PIPELINE_MODE=daily" \
  --set-secrets "EODHD_API_KEY=eodhd-api-key:latest,DB_URL=commodities-db-url:latest,SLACK_WEBHOOK_URL=slack-webhook-url:latest" \
  --region <REGION>
```

### 6. Create Cloud Scheduler Job (Daily at 9 PM EST)

```bash
gcloud scheduler jobs create http commodities-daily-schedule \
  --location <REGION> \
  --schedule "0 21 * * 1-5" \
  --time-zone "America/New_York" \
  --uri "https://<REGION>-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/<PROJECT_ID>/jobs/commodities-daily:run" \
  --http-method POST \
  --oauth-service-account-email "<PROJECT_NUMBER>-compute@developer.gserviceaccount.com"
```

### 7. Run Backfill (one-time)

```bash
gcloud run jobs execute commodities-backfill --region <REGION>
```

---

## API Cost Analysis

| Mode | Calls per Run | Frequency | Calls per Day |
|------|--------------|-----------|---------------|
| Backfill | 42 | One-time | 42 |
| Daily | 42 | Mon-Fri | 42 |
| **Total** | | | **42** |

EODHD limit: 200,000 calls/day. We use **0.021%** of the daily limit.
